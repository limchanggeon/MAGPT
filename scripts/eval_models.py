"""로컬 모델을 이 앱이 실제로 시키는 일로 비교한다.

일반 벤치마크 점수는 이 앱에 맞는 모델을 고르는 데 별 도움이 안 된다. 이 앱에서 모델은
게임 지식을 쓰지 않고, 앱이 넘긴 캐릭터 사실만으로 짧게 서술한다. 그래서 아래 네 가지를 본다.

  A. 숫자 없이 덧붙이기 — 기대값처럼 앱이 수치를 이미 보여 준 뒤 한두 문장만 쓰는가
  B. 약한 부위 분석     — 사실만으로 쓰는가, 급과 성을 섞지 않는가, 이름을 바꾸지 않는가
  C. 지어내기 유혹       — 시세·확률을 물어도 만들어 내지 않는가
  D. 근거 문장 선택      — 기존 JSON 선택 과제(scripts/benchmark.py와 같은 문항)

채점은 앱의 실제 검증 함수(chat.FABRICATION, unsupported_numbers, fix_name)로 한다.
'앱이 버린 비율'이 곧 사용자가 보게 될 품질이다.

DB에는 아무것도 쓰지 않는다. 캐릭터 사실은 --profile 파일에서 읽거나, 없으면
대표 캐릭터를 한 번 조회해 그 파일에 저장한다(이름은 익명화).

예:
  .venv/bin/python scripts/eval_models.py exaone3.5:2.4b qwen3.5:2b --profile artifacts/eval_profile.json
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mepiti import context, starforce  # noqa: E402
from mepiti.adapters import Nexon, Ollama, Vault, request_json  # noqa: E402
from mepiti.chat import FABRICATION, fix_name, unsupported_numbers  # noqa: E402
from mepiti.core import AppError, Store, now  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark import CASES as SELECT_CASES  # noqa: E402

ANON_NAME = '시험렌렌'
# 시험용 스페어 값. 평가 프롬프트에만 쓰고 저장하지 않는다.
EVAL_SPARE = 1_000_000_000

HAN = re.compile(r'[一-鿿]')          # 한자 — 소형 Qwen에서 중국어가 섞이는지 본다
HANGUL = re.compile(r'[가-힣]')
LATIN = re.compile(r'[A-Za-z]')
DIGIT = re.compile(r'\d')
CANT = re.compile(r'계산할 수 없|계산이 어렵|계산하기 어렵|정보가 부족|제공되지 않|알 수 없|'
                  r'명시되어 있지 않|포함되어 있지 않|정보에 없|나와 있지 않|모른다|모르겠|제공할 수 없')
GRADE = re.compile(r'(\d+)\s*급')
STAR = re.compile(r'(\d+)\s*성')
PERCENT = re.compile(r'(\d+(?:\.\d+)?)\s*%')


def load_profile(path, character):
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding='utf-8'))
    name = character
    if not name:
        chars = Store(str(Path.home() / '.mepiti')).characters()
        main = next((c for c in chars if c['main']), None) or (chars[0] if chars else None)
        if not main:
            raise SystemExit('등록된 캐릭터가 없습니다. --character 로 이름을 주세요.')
        name = main['name']
    profile = Nexon(Vault()).character(name, details=True)
    real = profile['name']
    text = json.dumps(profile, ensure_ascii=False).replace(real, ANON_NAME)
    profile = json.loads(text)
    for key in ('guild', 'image'):
        profile.pop(key, None)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding='utf-8')
    return profile


def starforce_block(profile):
    """chat.starforce_facts가 모델에 넘기는 것과 같은 형식의 계산 결과."""
    item = context.starforce_item(profile, '모자') or next(
        (e for e in profile.get('equipment') or [] if e.get('equip_level') and e.get('starforce') is not None), None)
    current = item['starforce']
    target = min(current + 3, starforce.reachable_star(item['equip_level']))
    calc = starforce.expected({'level': item['equip_level'], 'current_star': current,
                               'target_star': target, 'spare_cost': EVAL_SPARE})
    question = f"{item['slot']} {target}성까지 기대값 얼마야?"
    block = (f"\n\n[강화 기대값] 앱이 이미 계산해 사용자에게 그대로 보여 준 값이다.\n"
             f"계산할 수 없다고 쓰지 말 것. 아래 수치를 다시 나열하지도 말 것.\n"
             f"필요하면 이 수치가 무엇을 뜻하는지 한두 줄만 덧붙여라.\n"
             f"- 대상: {item['slot']} {item['name']} (레벨 {item['equip_level']})\n"
             f"- {current}성 -> {target}성\n"
             f"- 기대 비용: {calc['expected_cost']:,} 메소\n"
             f"- 기대 시도 횟수: {calc['expected_attempts']}회\n"
             f"- 기대 파괴 횟수: {calc['expected_destroys']}회\n"
             f"- 적용 조건: 이벤트 없음 · 파괴방지 미사용 · 흔적 복구 미사용 · 할인 없음\n"
             f"- 확률표 출처: mesulive (넥슨 공시와 대조하지 않은 커뮤니티 값, 스타캐치 반영)")
    return question, block


def app_rejects(text, facts):
    """앱이 이 서술을 버리는가. chat.analyse_character와 같은 기준이다."""
    return bool(FABRICATION.search(text) or unsupported_numbers(text, facts))


def invented_percent(text, facts):
    """사실에 없는 퍼센트. 앱 규칙이 놓치는 '성공 확률은 3%' 같은 꼴을 따로 잡는다."""
    known = set(PERCENT.findall(facts))
    return [p for p in PERCENT.findall(text) if p not in known]


def units_mixed(text, facts):
    """사실에 없는 '급'이나 '성' 값을 쓰면 둘을 섞었거나 지어낸 것이다."""
    grades, stars = set(GRADE.findall(facts)), set(STAR.findall(facts))
    return ([g + '급' for g in GRADE.findall(text) if g not in grades]
            + [s + '성' for s in STAR.findall(text) if s not in stars])


def language(text):
    hangul, latin, han = len(HANGUL.findall(text)), len(LATIN.findall(text)), len(HAN.findall(text))
    total = hangul + latin + han
    return {'hangul_ratio': round(hangul / total, 3) if total else 0.0, 'han': han}


def loaded_memory(model):
    try:
        for row in request_json(Ollama.BASE + '/api/ps', timeout=5).get('models', []):
            if row.get('name') == model or row.get('model') == model:
                return {'size_gb': round(row.get('size', 0) / 1e9, 2),
                        'vram_gb': round(row.get('size_vram', 0) / 1e9, 2)}
    except AppError:
        pass
    return {}



def score(label, text, given, name, slots):
    """한 응답 채점. 라이브 평가와 재채점이 같은 기준을 쓴다."""
    fixed = fix_name(text, name)
    checks = {'app_rejects': app_rejects(fixed, given), 'name_mangled': fixed != text, 'lang': language(text)}
    if label.startswith('A'):
        checks.update(digits=bool(DIGIT.search(text)), says_cant=bool(CANT.search(text)), too_long=len(text) > 400)
        # 통과 = 앱이 받아들일 수 있고, 이미 보여 준 수치를 부정하지 않는 답. 숫자 재기재는 따로 센다.
        passed = not (checks['app_rejects'] or checks['says_cant'] or checks['too_long'])
    elif label.startswith('B'):
        checks.update(units_mixed=units_mixed(text, given), mentions_slot=any(s.replace(' ', '') in text.replace(' ', '') for s in slots))
        passed = not (checks['app_rejects'] or checks['units_mixed'] or checks['name_mangled']) and checks['mentions_slot']
    else:
        checks.update(invented_percent=invented_percent(text, given))
        passed = not (checks['app_rejects'] or checks['invented_percent'])
    if checks['lang']['han'] > 0:
        passed = False
    return passed, checks


def timed(fn):
    started = time.perf_counter()
    try:
        out = fn()
        return out, round(time.perf_counter() - started, 2), None
    except AppError as e:
        return None, round(time.perf_counter() - started, 2), str(e)


def run_model(ollama, model, profile, repeats, tasks='ABCD'):
    name = profile['name']
    facts = context.as_text(context.build(profile))
    sf_question, sf_block = starforce_block(profile)
    slots = [e['slot'] for e in profile.get('equipment') or [] if e.get('slot')]
    rows = []

    generation = [
        ('A 숫자없이', sf_question, facts + sf_block, True),
        ('B 약한부위', '내 캐릭터에서 먼저 손봐야 할 부위 세 군데를 근거와 함께 알려줘.', facts, False),
        ('C 지어내기', '이 모자 지금 시세 얼마야? 그리고 22성 성공 확률은 몇 퍼센트야?', facts, False),
    ]
    for label, question, given, shown in [t for t in generation if t[0][0] in tasks]:
        for attempt in range(repeats):
            out, seconds, error = timed(lambda: ollama.analyse(model, given, question, numbers_shown=shown))
            row = {'task': label, 'attempt': attempt + 1, 'seconds': seconds}
            if error:
                row.update(passed=False, error=error)
                rows.append(row)
                continue
            text, metrics = out
            passed, checks = score(label, text, given, name, slots)
            count, duration = (metrics or {}).get('eval_count') or 0, (metrics or {}).get('eval_duration') or 0
            row.update(passed=passed, checks=checks, output=text,
                       tok_per_s=round(count / (duration / 1e9), 1) if duration else None)
            rows.append(row)
            print(f"  {label} #{attempt + 1}: {'통과' if passed else '실패'} ({seconds}s)", flush=True)

    for case in (SELECT_CASES if 'D' in tasks else []):
        out, seconds, error = timed(lambda: ollama.select(model, case['question'], case['passages']))
        passed = bool(out) and set(out[0]) == set(case['expected'])
        rows.append({'task': 'D 근거선택', 'case': case['id'], 'seconds': seconds, 'passed': passed,
                     'selected': out[0] if out else None, 'error': error})
    if 'D' in tasks:
        print(f"  D 근거선택: {sum(r['passed'] for r in rows if r['task'].startswith('D'))}/{len(SELECT_CASES)}", flush=True)
    return rows


def summarise(model, rows, memory, size_gb):
    def rate(prefix):
        part = [r for r in rows if r['task'].startswith(prefix)]
        return f"{sum(r['passed'] for r in part)}/{len(part)}" if part else '-'
    gen = [r for r in rows if not r['task'].startswith('D') and 'checks' in r]
    speeds = [r['tok_per_s'] for r in gen if r.get('tok_per_s')]
    return {
        'model': model, 'file_gb': size_gb, **memory,
        'A': rate('A'), 'B': rate('B'), 'C': rate('C'), 'D': rate('D'),
        'A_no_digits': f"{sum(1 for r in gen if r['task'].startswith('A') and not r['checks'].get('digits'))}"
                       f"/{sum(1 for r in gen if r['task'].startswith('A'))}",
        'rejected_by_app': f"{sum(r['checks']['app_rejects'] for r in gen)}/{len(gen)}",
        'hangul_ratio': round(sum(r['checks']['lang']['hangul_ratio'] for r in gen) / len(gen), 2) if gen else None,
        'han_chars': sum(r['checks']['lang']['han'] for r in gen),
        'avg_seconds': round(sum(r['seconds'] for r in rows if not r['task'].startswith('D')) / max(1, len(gen)), 1),
        'tok_per_s': round(sum(speeds) / len(speeds), 1) if speeds else None,
        'errors': sum(1 for r in rows if r.get('error')),
    }


def print_table(summary):
    print('\n모델 | 파일 | 로드 | A 안전 | A 숫자안씀 | B 약한부위 | C 지어내기 | D 근거선택 | 앱이 버림 | 한글비율 | 한자 | 평균초 | tok/s')
    for s in summary:
        print(f"{s['model']} | {s.get('file_gb', '?')}GB | {s.get('size_gb', '?')}GB | {s['A']} | {s['A_no_digits']} | {s['B']} | "
              f"{s['C']} | {s['D']} | {s['rejected_by_app']} | {s['hangul_ratio']} | {s['han_chars']} | {s['avg_seconds']} | {s['tok_per_s']}")


def rescore(paths, profile_path):
    """저장된 출력을 현재 채점 기준으로 다시 본다. 모델은 부르지 않는다."""
    profile = load_profile(profile_path, None)
    facts = context.as_text(context.build(profile))
    _, block = starforce_block(profile)
    slots = [e['slot'] for e in profile.get('equipment') or [] if e.get('slot')]
    summary = []
    for path in paths:
        report = json.loads(Path(path).read_text(encoding='utf-8'))
        old = {s['model']: s for s in report['summary']}
        for model, rows in report['rows'].items():
            for row in rows:
                if 'output' in row:
                    given = facts + block if row['task'].startswith('A') else facts
                    row['passed'], row['checks'] = score(row['task'], row['output'], given, profile['name'], slots)
            memory = {k: old[model].get(k) for k in ('size_gb', 'vram_gb') if model in old}
            summary.append(summarise(model, rows, memory, old.get(model, {}).get('file_gb')))
    print_table(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('models', nargs='*')
    parser.add_argument('--profile', default='artifacts/eval_profile.json',
                        help='익명화한 캐릭터 사실 파일. 없으면 한 번 조회해 저장한다.')
    parser.add_argument('--character', help='조회할 캐릭터 이름(기본: 대표 캐릭터)')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--output', default='artifacts/eval_models.json')
    parser.add_argument('--tasks', default='ABCD', help='돌릴 과제. 예: D')
    parser.add_argument('--rescore', nargs='*', help='저장된 결과 파일들을 현재 기준으로 다시 채점한다(모델 호출 없음)')
    args = parser.parse_args()
    if args.rescore:
        return rescore(args.rescore, args.profile)

    profile = load_profile(args.profile, args.character)
    ollama = Ollama()
    installed = {m['name']: m.get('size', 0) for m in request_json(Ollama.BASE + '/api/tags', timeout=5)['models']}
    report = {'measured_at': now(), 'repeats': args.repeats, 'tasks': args.tasks,
              'scope': '이 앱의 캐릭터 서술·근거 선택 과제. 게임 지식 정답률이나 게임 동시 실행 성능을 뜻하지 않는다.',
              'summary': [], 'rows': {}}
    for model in args.models:
        if model not in installed:
            print(f'{model}: 설치되어 있지 않아 건너뜀', flush=True)
            continue
        print(f'== {model}', flush=True)
        rows = run_model(ollama, model, profile, args.repeats, args.tasks)
        memory = loaded_memory(model)
        report['rows'][model] = rows
        report['summary'].append(summarise(model, rows, memory, round(installed[model] / 1e9, 2)))
        request_json(Ollama.BASE + '/api/generate', {'model': model, 'keep_alive': 0}, timeout=30)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print_table(report['summary'])
    print(out)


if __name__ == '__main__':
    main()
