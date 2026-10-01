"""목표 전투력대 비교 상담 — 대화에서 자연스럽게 묻고 답하게 한다.

모델은 계산하지 않는다. 질문을 읽어 앱이 필요한 계산을 골라 돌리고(부위 후보, 특정 장비, 세트 맞추기, 여러 부위 함께,
가성비), 그 결과를 사실로 넘긴다. 모델은 그 사실을 상담하듯 풀어 쓴다(adapters.analysis_messages의 consult 문체).

예:
  "목표 2억5천이면 뭐부터 바꿔?"      → 기본 비교 + 이득 순
  "반지는 뭘로 바꿔?"                 → 반지 자리 후보별 교체 효과
  "몽환의 벨트로 바꾸면?"             → 그 장비 교체 효과
  "칠흑 세트 맞추면 얼마나 올라?"      → 칠흑 장비를 함께 바꿀 때(세트 효과 합쳐서)
  "추천대로 다 바꾸면?"               → 이득인 교체를 함께
  "가성비 좋은 건?" / "30억으로 뭐 해?" → 아는 노작값으로 1억당 효과
"""
import re

from . import peers, prices, statcalc

SLOT_GROUPS = {'반지': ('반지1', '반지2', '반지3', '반지4'), '펜던트': ('펜던트', '펜던트2')}
SLOT_ALIASES = {'목걸이': '펜던트', '펜던트': '펜던트', '귀걸이': '귀고리', '귀고리': '귀고리', '이어링': '귀고리',
                '얼장': '얼굴장식', '얼굴장식': '얼굴장식', '눈장': '눈장식', '눈장식': '눈장식', '어깨': '어깨장식',
                '견장': '어깨장식', '보조': '보조무기', '보조무기': '보조무기', '심장': '기계 심장', '하트': '기계 심장',
                '엠블': '엠블렘', '엠블렘': '엠블렘', '모자': '모자', '상의': '상의', '하의': '하의', '한벌옷': '한벌옷',
                '신발': '신발', '장갑': '장갑', '망토': '망토', '벨트': '벨트', '반지': '반지', '포켓': '포켓 아이템'}
SET_WORDS = {'칠흑': '칠흑의 보스', '여명': '여명의 보스', '광휘': '광휘의 보스', '보장': '보스 장신구', '보스 장신구': '보스 장신구',
             '에테르넬': '에테르넬', '아케인': '아케인셰이드', '앱솔': '앱솔랩스', '도전자': '도전자의', '루타': '루타비스'}
ALL_WORDS = re.compile(r'(?:다|전부|모두|한꺼번에|싹)\s*(?:바꾸|바꿔|맞추|맞춰|교체)|추천(?:한\s*거|대로)|이득인\s*(?:거|것)\s*(?:다|전부)')
MONEY_WORDS = re.compile(r'가성비|예산|돈|비용|얼마|가격|싸|비싸|\d+\s*억\s*(?:으로|정도|있)')
# 이름에서 흔한 말은 장비를 가리키는 단서로 쓰지 않는다('링', '마크' 등).
GENERIC = {'링', '마크', '이어링', '펜던트', '벨트', '세트', '나이트', '워리어', '반지', '목걸이', '뱃지', '엠블렘', '하트',
           '숄더', '케이프', '글러브', '슈즈', '헬름', '아머', '팬츠', '마도서', '전사', '마법사', '궁수', '도적', '해적', ':'}
# 상담 대화를 이어 가는 짧은 후속 말.
FOLLOWUP = re.compile(r'^(?:그\s*다음|다음은|그럼|그러면|왜|이유|어떤\s*게|뭐가|그건|그거|그중|둘\s*중|더\s*(?:싼|좋은)|'
                      r'얼마나|몇\s*%|반지|펜던트|벨트|모자|장갑|신발|망토|어깨|귀|눈장|얼장|보조|엠블|심장|칠흑|여명|광휘|에테르넬|아케인|'
                      r'가성비|예산|\d+\s*억)')
CP_IN_TEXT = re.compile(r'(\d+(?:\.\d+)?\s*억(?:\s*\d+\s*천?(?:만)?)?)')


def target_cp_in(question):
    """질문에 적힌 목표 전투력('목표 2억5천', '전투력 3억'). 없으면 None."""
    if not re.search(r'전투력|목표|전투|까지', question):
        return None
    m = CP_IN_TEXT.search(question)
    return peers.parse_cp(m.group(1)) if m else None


def my_items(ledger):
    out = {}
    for item in getattr(ledger, 'items', None) or []:
        out.setdefault(item.get('item_equipment_slot'), item)
    return out


def slots_in(question, available):
    found = []
    for word, slot in sorted(SLOT_ALIASES.items(), key=lambda x: -len(x[0])):
        if word in question:
            group = SLOT_GROUPS.get(slot, (slot,))
            for s in group:
                if s in available and s not in found:
                    found.append(s)
    return found


def items_in(question, names):
    """질문에 나온 장비(비교 유저들이 낀 장비 이름에서 찾는다). 이름 전체 또는 흔하지 않은 낱말 하나."""
    hits = []
    for name in names:
        if name in question:
            hits.append(name)
            continue
        words = [w for w in re.split(r'[\s:]+', name) if len(w) >= 2 and w not in GENERIC]
        words = [w[:-1] if w.endswith('의') and len(w) > 2 else w for w in words]     # '몽환의' → '몽환'
        if any(w in question for w in words):
            hits.append(name)
    return hits


def sets_in(question, set_names):
    out = []
    for word, prefix in SET_WORDS.items():
        if word in question:
            out += [s for s in set_names if s.startswith(prefix) and s not in out]
    return out


def best_slot(ledger, mine, item, slots, people=None):
    """장비를 어느 자리에 끼울 때 가장 좋은가(반지·펜던트처럼 자리가 여럿인 부위). (자리, 결과, 넣은 한 벌).

    그 자리에 그 장비를 낀 비교 유저가 있으면 그 자리의 중앙 한 벌을 쓴다(부위별 요약과 같은 값이 나오게)."""
    best = None
    for slot in slots:
        if slot not in mine or statcalc.is_lucky(mine[slot].get('item_name')):
            continue
        pick = (peers.candidate(people, slot, item['item_name']) if people else None) or item
        result = statcalc.swap(ledger, mine[slot], pick)
        if best is None or result['boss_range'][1] > best[1]['boss_range'][1]:
            best = (slot, result, pick)
    return best


def slots_for(slot):
    for group in SLOT_GROUPS.values():
        if slot in group:
            return group
    return (slot,)


def pct(r):
    return f"{r[0]:+.2f}%~{r[1]:+.2f}%" if r[0] != r[1] else f"{r[0]:+.2f}%"


def line(slot, item, result, mine):
    name, star = item['item_name'], int(item.get('starforce') or 0)
    pots = ', '.join(item[k] for k in ('potential_option_1', 'potential_option_2', 'potential_option_3') if item.get(k)) or '잠재 없음'
    what = (f"같은 {name}를 {star}성·{pots} 수준으로 맞추면" if mine and mine.get('item_name') == name
            else f"{(mine or {}).get('item_name') or '빈 자리'} → {name} {star}성({pots})으로 바꾸면")
    extra = (' · 세트 ' + ', '.join(result['sets'])) if result['sets'] else ''
    unknown = (' · 모름: ' + '; '.join(result['unknown'])) if result['unknown'] else ''
    return f"- {slot}: {what} 스탯공격력 {pct(result['range'])}, 보스 기준 {pct(result['boss_range'])}{extra}{unknown}"


def pool_items(people):
    """비교 유저들이 낀 장비: 이름 → (그 이름이 나온 부위 목록, 옵션이 저장된 한 벌들)."""
    out = {}
    for person in people:
        for slot, s in person.items():
            if not s.get('item'):
                continue
            entry = out.setdefault(s['item']['item_name'], {'slots': set(), 'items': []})
            entry['slots'].add(slot)
            entry['items'].append(s['item'])
    return out


def representative(entry):
    items = sorted(entry['items'], key=lambda i: int(i.get('starforce') or 0))
    return items[len(items) // 2]


def build(store, profile, ledger, question, managed=None):
    """상담용 사실 글과 화면 안내를 만든다. (글, 안내 목록, compare 결과)."""
    compared = peers.compare(store, profile, ledger=ledger)
    notes = []
    if not compared.get('ready'):
        return '', notes, compared
    text = [peers.facts_text(compared)]
    if ledger is None:
        text.append('[교체 시뮬레이션] 아직 내 스탯 출처를 받지 못해 교체 효과를 계산하지 못했다. 수치 없이 통계로만 말할 것.')
        return '\n'.join(text), notes, compared
    people = peers.stored(store, compared['target'])
    pool = pool_items(people)
    mine = my_items(ledger)
    focus = []
    # 1) 질문한 부위: 그 자리에 비교 유저들이 많이 끼는 장비 상위 3개를 각각 넣어 본다.
    stats = peers.slot_stats(people)
    groups = []
    for slot in slots_in(question, set(mine) | set(stats)):
        if slots_for(slot) not in groups:
            groups.append(slots_for(slot))
    for group in groups:
        label = next((k for k, v in SLOT_GROUPS.items() if v == group), group[0])
        names = {}
        for s in group:
            for it in (stats.get(s) or {}).get('items', []):
                names[it['name']] = names.get(it['name'], 0) + it['share']
        rows = []
        for name in sorted(names, key=lambda n: -names[n])[:3]:
            if name not in pool:
                continue
            picked = best_slot(ledger, mine, representative(pool[name]), group, people)
            if picked:
                rows.append(line(picked[0], picked[2], picked[1], mine.get(picked[0])))
        if rows:
            focus.append(f"[질문한 부위: {label}] 목표 전투력대 유저가 많이 끼는 장비를 넣어 본 결과(한 부위만, 자리가 여럿이면 가장 좋은 자리):")
            focus += rows
    # 2) 질문에 나온 장비.
    for name in items_in(question, list(pool))[:4]:
        slots = set()
        for s in pool[name]['slots']:
            slots.update(slots_for(s))
        picked = best_slot(ledger, mine, representative(pool[name]), sorted(slots), people)
        if picked:
            focus.append(f"[질문한 장비: {name}]")
            focus.append(line(picked[0], picked[2], picked[1], mine.get(picked[0])))
    # 3) 세트 맞추기 / 추천대로 다: 여러 부위를 함께 바꿔 세트 효과를 합쳐 계산한다.
    combos = []
    for set_name in sets_in(question, list(getattr(ledger, 'sets', {}))):
        pairs, labels, used = [], [], set()      # 같은 장비는 하나만(보스 장신구 등은 중복 착용 불가)
        for slot, st in stats.items():
            if slot not in mine or statcalc.is_lucky(mine[slot].get('item_name')):
                continue
            if statcalc.set_of(mine[slot].get('item_name'), [set_name]):
                continue                      # 이미 그 세트
            names = [i['name'] for i in st['items'] if statcalc.set_of(i['name'], [set_name]) and i['name'] in pool
                     and i['name'] not in used and i['name'] not in {m.get('item_name') for m in mine.values()}]
            if names:
                used.add(names[0])
                pick = peers.candidate(people, slot, names[0]) or representative(pool[names[0]])
                pairs.append((mine[slot], pick))
                labels.append(f"{slot} {mine[slot].get('item_name')}→{pick['item_name']} {int(pick.get('starforce') or 0)}성")
        if pairs:
            combos.append((f'{set_name} 맞추기', pairs, labels))
    if ALL_WORDS.search(question) and compared.get('upgrades'):
        pairs, labels, used = [], [], set()
        for up in compared['upgrades']:
            slot = up['slot']
            pick = peers.candidate(people, slot, up['item'])
            if up['item'] in used or (not up.get('same_item') and up['item'] in {m.get('item_name') for m in mine.values()}):
                continue
            used.add(up['item'])
            if pick and slot in mine:
                pairs.append((mine[slot], pick))
                labels.append(f"{slot} {up['item']} {up['starforce']}성")
        if len(pairs) > 1:
            combos.append(('이득인 교체 전부', pairs, labels))
    for title, pairs, labels in combos:
        result = statcalc.swap_many(ledger, pairs)
        extra = (' · 세트 ' + ', '.join(result['sets'])) if result['sets'] else ''
        focus.append(f"[함께 바꾸면: {title}] {len(pairs)}부위({'; '.join(labels)})를 한꺼번에 바꾸면 "
                     f"스탯공격력 {pct(result['range'])}, 보스 기준 {pct(result['boss_range'])}{extra}. "
                     '이 값은 세트 효과를 합쳐 앱이 계산한 것이며, 부위별 값을 더한 것과 다르다.')
    # 4) 가성비·예산: 아는 노작값(저장값·경매장)으로 1억당 보스 기준 효과. 모르는 값은 모른다고 넘긴다.
    if MONEY_WORDS.search(question):
        targets = [u for u in (compared.get('upgrades') or [])][:6]
        resolved = {r['item']: r for r in prices.resolve_many(store, [(u['item'], None) for u in targets])}
        rows, unknown = [], []
        for u in targets:
            r = resolved.get(u['item'])
            if r and r.get('known') and r.get('price'):
                per = u['boss_range'][1] / (r['price'] / 1e8)
                rows.append((per, f"- {u['slot']} {u['item']}: 노작값 {r['price'] / 1e8:.2f}억({r.get('source')}) · 보스 기준 {pct(u['boss_range'])} · 1억당 약 {per:.2f}%"))
            else:
                unknown.append(u['item'])
        if rows:
            focus.append('[가성비] 아는 노작값 기준(강화·잠재 비용은 빠짐, 1억당 값이 클수록 효율적):')
            focus += [r for _, r in sorted(rows, key=lambda x: -x[0])]
        if unknown:
            focus.append('[값 모름] ' + ', '.join(unknown) + ' — 노작값을 모르니 가격 비교를 하지 말고, 값을 알려 주면 계산한다고 말할 것.')
        budget = (managed or {}).get('budget')
        if budget:
            focus.append(f"[예산] 사용자가 캐릭터 화면에 저장한 예산 {budget / 1e8:.2f}억 메소.")
    if focus:
        text.insert(1, '\n'.join(focus))
    return '\n'.join(text), notes, compared
