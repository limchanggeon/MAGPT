"""유니온 공격대원 효과와 '다음에 뭐 키울까' 추천.

효과 수치와 평가(S~F)는 사용자가 제공한 커뮤니티 위키 표를 옮겼다(2026-09-28).
**넥슨 공식 수치를 이 앱이 검증한 것이 아니며, 평가는 커뮤니티 의견이다.** 결과에 출처를 함께 표시한다.

등급 레벨 기준(B 60 / A 100 / S 140 / SS 200 / SSS 250)은 일반 공격대원 기준이다.
계정의 캐릭터 목록(넥슨 character/list)으로 직업별 최고 레벨을 찾아 현재 등급을 정한다.
"""
from .core import AppError

SOURCE = {'name': '사용자 제공 커뮤니티 위키 표(유니온 공격대원 효과)', 'official': False,
          'recorded': '2026-09-28'}

GRADES = (('B', 60), ('A', 100), ('S', 140), ('SS', 200), ('SSS', 250))

STAT_10 = (10, 20, 40, 80, 100)
PCT_2_6 = (2, 3, 4, 5, 6)
PCT_1_6 = (1, 2, 3, 5, 6)

# 효과 종류: (표시 이름, 등급별 수치, 단위)
EFFECTS = {
    'STR': ('STR', STAT_10, ''), 'DEX': ('DEX', STAT_10, ''),
    'INT': ('INT', STAT_10, ''), 'LUK': ('LUK', STAT_10, ''),
    'hp_pct': ('최대 HP', PCT_2_6, '%'), 'mp_pct': ('최대 MP', PCT_2_6, '%'),
    'hp_flat': ('최대 HP', (250, 500, 1000, 2000, 2500), ''),
    'crit_rate': ('크리티컬 확률', (1, 2, 3, 4, 5), '%'),
    'summon': ('소환수 지속시간', (4, 6, 8, 10, 12), '%'),
    'hp_recover': ('적 타격 시 70% 확률로 순수 HP 회복', (2, 4, 6, 8, 10), '%'),
    'mp_recover': ('적 타격 시 70% 확률로 순수 MP 회복', (2, 4, 6, 8, 10), '%'),
    'cooldown': ('스킬 재사용 대기시간 감소', PCT_2_6, '%'),
    'meso': ('메소 획득량', (1, 2, 3, 4, 5), '%'),
    'crit_dmg': ('크리티컬 데미지', PCT_1_6, '%'),
    'ied': ('방어율 무시', PCT_1_6, '%'),
    'resist': ('상태 이상 내성', (1, 2, 3, 4, 5), ''),
    'boss': ('보스 공격 시 데미지', PCT_1_6, '%'),
    'proc_dmg': ('공격 시 20% 확률로 데미지', (4, 8, 12, 16, 20), '%'),
    'buff': ('버프 지속 시간', (5, 10, 15, 20, 25), '%'),
    'xenon': ('STR·DEX·LUK 각각', (5, 10, 20, 40, 50), ''),
    'speed': ('이동속도·최대 이동속도', (2, 4, 6, 8, 10), ''),
    'exp': ('경험치 획득량', (4, 6, 8, 10, 12), '%'),
    'lete': ('올스탯 각각(최대 HP도 500~2500)', (10, 20, 30, 40, 50), ''),
}

# 직업 -> 효과 종류. 넥슨 character_class 이름과 같아야 한다.
JOBS = {
    # 모험가
    '히어로': 'STR', '팔라딘': 'STR', '다크나이트': 'hp_pct',
    '아크메이지(불,독)': 'mp_pct', '아크메이지(썬,콜)': 'INT', '비숍': 'INT',
    '보우마스터': 'DEX', '패스파인더': 'DEX', '신궁': 'crit_rate',
    '나이트로드': 'crit_rate', '섀도어': 'LUK', '듀얼블레이더': 'LUK',
    '캡틴': 'summon', '바이퍼': 'STR', '캐논마스터': 'STR',
    # 시그너스
    '소울마스터': 'hp_flat', '미하일': 'hp_flat', '플레임위자드': 'INT',
    '윈드브레이커': 'DEX', '나이트워커': 'LUK', '스트라이커': 'STR',
    # 영웅
    '아란': 'hp_recover', '에반': 'mp_recover', '루미너스': 'INT',
    '메르세데스': 'cooldown', '팬텀': 'meso', '은월': 'crit_dmg',
    # 레지스탕스
    '블래스터': 'ied', '데몬슬레이어': 'resist', '데몬어벤져': 'boss',
    '배틀메이지': 'INT', '와일드헌터': 'proc_dmg', '메카닉': 'buff', '제논': 'xenon',
    # 노바
    '카이저': 'STR', '카인': 'DEX', '카데나': 'LUK', '엔젤릭버스터': 'DEX',
    # 레프
    '아델': 'STR', '일리움': 'INT', '칼리': 'LUK', '아크': 'STR',
    # 아니마
    '렌': 'speed', '라라': 'INT', '호영': 'LUK',
    # 단독
    '제로': 'exp', '키네시스': 'INT', '레테': 'lete',
}
# 표기가 다른 이름. 표(나무위키)와 API 이름이 다를 수 있다.
ALIASES = {'캐논슈터': '캐논마스터', '듀얼블레이드': '듀얼블레이더', '데몬 슬레이어': '데몬슬레이어',
           '데몬 어벤져': '데몬어벤져', '엔젤릭 버스터': '엔젤릭버스터'}

# 표의 한 줄 평. 추천 결과에 이유로 붙인다.
NOTES = {
    'crit_rate': '크리티컬 확률 100%를 맞춰야 하는데, 신궁·나이트로드로 최대 10%를 수급할 수 있다.',
    'crit_dmg': '크리티컬 데미지를 직접 올리는 효과라 우선순위가 가장 높다고 평가된다.',
    'cooldown': '%로 줄여 쿨타임이 긴 스킬일수록 효과가 크다. 극딜 주기가 당겨진다.',
    'boss': '보스 화력을 올리는 좋은 옵션. SS→SSS는 1%p만 올라 급하지 않다.',
    'ied': '한 번에 오르는 수치가 작지만 공격대원 효과 중에서는 준수하다.',
    'buff': '다크나이트·카이저처럼 버프 지속시간이 성능과 직결되는 직업에 필수.',
    'speed': '화력과 무관하지만 이동속도·최대 이동속도 상한을 함께 올린다.',
    'exp': '영구적으로 버닝 한 단계 정도의 경험치를 더해 준다.',
    'meso': '재화 획득에 직접 이익을 주는 유일한 효과(사냥 시).',
    'proc_dmg': '평균 0.8~4% 데미지 증가로 보면 된다. 사냥에도 적용.',
    'summon': '캡틴·호영처럼 소환수가 주력이거나 라라에게 중요하다.',
    'hp_pct': '데몬어벤져용. 다른 직업에는 보스전에서 거의 의미가 없다.',
    'hp_flat': '데몬어벤져용이나 HP% 효과가 적용되지 않아 효과가 작다.',
    'mp_pct': 'MP 소모가 심한 직업에 약간 도움이 된다.',
    'hp_recover': '보스전에서 한 틱 회복으로 사는 경우가 가끔 있다.',
    'mp_recover': 'MP 소모가 심한 직업의 극딜 부담을 약간 줄인다.',
    'resist': '상태 이상을 거는 보스에서 약간 도움이 된다.',
    'xenon': '제논에게는 좋지만 주스탯이 하나인 직업에는 효율이 반토막.',
    'lete': '제논·데몬어벤져에게 좋고, 다른 직업에는 효율이 반토막.',
}

STAT_JOBS = {  # 주스탯 판단이 API 능력치로 어려운 직업
    '제논': 'XENON', '데몬어벤져': 'HP',
}
SUB_STAT = {'STR': 'DEX', 'DEX': 'STR', 'LUK': 'DEX', 'INT': 'LUK'}
BUFF_JOBS = {'다크나이트', '카이저'}
SUMMON_JOBS = {'캡틴', '호영'}
RANK = {'S': 5, 'A': 4, 'B': 3, 'C': 2, 'D': 1, 'F': 0}


def canonical(job):
    return ALIASES.get(job, job) if job else job


def grade_index(level):
    """공격대원 레벨 -> 등급 번호(0=B ... 4=SSS). 60 미만이면 None."""
    found = None
    for i, (_, need) in enumerate(GRADES):
        if level >= need:
            found = i
    return found


def rating(effect, my_job, main_stat):
    """표의 평가를 대표 캐릭터 기준으로 고른다."""
    my_job = canonical(my_job)
    special = STAT_JOBS.get(my_job)
    if effect in ('STR', 'DEX', 'INT', 'LUK'):
        if special == 'XENON':
            return 'A' if effect != 'INT' else 'F'
        if effect == main_stat:
            return 'A'
        if effect != 'INT' and SUB_STAT.get(main_stat) == effect:
            return 'C'
        return 'F'
    if effect == 'hp_pct':
        return 'S' if my_job == '데몬어벤져' else ('D' if my_job in ('다크나이트', '비숍') else 'F')
    if effect == 'hp_flat':
        return 'A' if my_job == '데몬어벤져' else ('D' if my_job in ('다크나이트', '비숍') else 'F')
    if effect in ('mp_pct',):
        return 'F' if special else 'D'
    if effect == 'mp_recover':
        return 'F' if special else 'D'
    if effect == 'summon':
        return 'A' if my_job == '라라' else ('B' if my_job in SUMMON_JOBS else 'F')
    if effect == 'buff':
        return 'S' if my_job in BUFF_JOBS else 'B'
    if effect == 'xenon':
        if my_job == '제논':
            return 'A'
        return 'D' if (main_stat == 'INT' or my_job == '데몬어벤져') else 'B'
    if effect == 'lete':
        return 'A' if my_job in ('제논', '데몬어벤져') else 'B'
    if effect == 'speed':
        return {'배틀메이지': 'D', '와일드헌터': 'F'}.get(my_job, 'S')
    return {'crit_rate': 'S', 'crit_dmg': 'S', 'cooldown': 'S', 'boss': 'S', 'exp': 'S', 'meso': 'S',
            'ied': 'A', 'proc_dmg': 'A', 'hp_recover': 'D', 'resist': 'D'}.get(effect, 'F')


def effect_text(effect, index):
    name, values, unit = EFFECTS[effect]
    return f"{name} +{values[index]}{unit}" if index is not None else f"{name} 없음"


def recommend(roster, my_job, main_stat, world=None, placed=None, limit=8):
    """계정 캐릭터 목록으로 직업별 최고 레벨을 찾고, 다음 등급을 올릴 가치가 큰 순서로 돌려준다.

    roster: [{'name','world','job','level'}] (넥슨 character/list)
    placed: 유니온에 배치된 직업 이름 집합(모르면 None)
    """
    if not my_job or not main_stat:
        raise AppError('대표 캐릭터의 직업과 주스탯을 확인할 수 없어 추천하지 않습니다.')
    best = {}
    for c in roster or []:
        if world and c.get('world') != world:
            continue      # 유니온은 월드별이다.
        job = canonical(c.get('job'))
        if job in JOBS and (job not in best or c['level'] > best[job]['level']):
            best[job] = c
    rows = []
    for job, effect in JOBS.items():
        if canonical(my_job) == job:
            continue      # 대표 캐릭터 자신은 추천 대상이 아니다.
        grade = rating(effect, my_job, main_stat)
        if grade == 'F':
            continue
        have = best.get(job)
        level = int(have['level']) if have else 0
        now = grade_index(level)
        if now == len(GRADES) - 1:
            continue      # 이미 SSS
        nxt = 0 if now is None else now + 1
        need = GRADES[nxt][1]
        rows.append({
            'job': job, 'effect': effect, 'rating': grade,
            'character': have['name'] if have else None, 'level': level,
            'grade': GRADES[now][0] if now is not None else '없음',
            'next_grade': GRADES[nxt][0], 'next_level': need, 'levels_left': need - level,
            'now_effect': effect_text(effect, now), 'next_effect': effect_text(effect, nxt),
            'placed': (job in placed) if placed is not None else None,
            'note': NOTES.get(effect),
        })
    # 평가가 높은 효과 먼저, 같은 평가면 다음 등급까지 남은 레벨이 적은 순서.
    rows.sort(key=lambda r: (-RANK[r['rating']], r['levels_left']))
    return rows[:limit]


def as_text(rows, my_job, main_stat, union_info=None):
    """앱이 직접 쓰는 추천 블록. 모델이 수치를 지어내지 않게 한다."""
    head = f"**다음에 키우면 좋은 공격대원** (기준: {my_job} · 주스탯 {main_stat})"
    if union_info and union_info.get('level'):
        head += f"\n유니온 레벨 {union_info['level']:,}" + (f" · {union_info['grade']}" if union_info.get('grade') else '')
    lines = [head, '']
    for i, r in enumerate(rows, 1):
        who = f"{r['character']} Lv.{r['level']}" if r['character'] else '캐릭터 없음'
        placed = '' if r['placed'] is None else (' · 배치됨' if r['placed'] else ' · 미배치')
        lines.append(f"{i}. **{r['job']}** [{r['rating']}] {who} ({r['grade']}{placed})")
        lines.append(f"   → {r['next_level']}레벨({r['next_grade']})까지 {r['levels_left']}레벨: "
                     f"{r['now_effect']} → {r['next_effect']}")
        if r['note']:
            lines.append(f"   {r['note']}")
    if not rows:
        lines.append('평가가 F가 아닌 효과는 이미 모두 SSS입니다.')
    return '\n'.join(lines)
