"""모델에게 넘길 캐릭터 사실 묶음.

여기 들어가는 값은 전부 넥슨 Open API가 돌려준 것이거나, 그 값으로 계산한 것이다.
게임 규칙·확률·시세 같은 주장은 넣지 않는다. 모델은 이 묶음 안의 사실만 근거로 쓸 수 있다.

추가옵션 등급('급', 'n추')은 커뮤니티 약식 기준이라 출처를 함께 적는다.
"""
from .adapters import integer

# 장비창 자리 순서대로 보여 주면 모델이 부위를 헷갈리지 않는다.
SLOT_ORDER = ['무기', '보조무기', '엠블렘', '모자', '상의', '하의', '한벌옷', '신발', '장갑', '망토',
              '어깨장식', '벨트', '얼굴장식', '눈장식', '귀고리', '펜던트', '펜던트2',
              '반지1', '반지2', '반지3', '반지4', '훈장', '뱃지', '포켓 아이템', '기계 심장',
              '칭호', '안드로이드']
KEY_STATS = ['전투력', 'STR', 'DEX', 'INT', 'LUK', 'HP', '최종 스탯공격력', '최대 스탯공격력',
             '데미지', '최종 데미지', '보스 몬스터 데미지', '방어율 무시', '크리티컬 확률',
             '크리티컬 데미지', '재사용 대기시간 감소 (초)', '재사용 대기시간 감소 (%)',
             '아케인포스', '어센틱포스', '스타포스', '공격력', '마력', '방어력', '상태이상 내성']


def stat_map(profile):
    return {s['name']: s['value'] for s in profile.get('stats') or []}


def equipment_lines(profile):
    """장비 한 줄씩. 강화 상태와 추가옵션 등급을 함께 적는다."""
    items = {e.get('slot'): e for e in profile.get('equipment') or []}
    lines = []
    for slot in SLOT_ORDER:
        item = items.pop(slot, None)
        if not item:
            continue
        parts = [f"{slot}: {item['name']}"]
        star, upgrade = item.get('starforce'), item.get('scroll_upgrade')
        if star:
            parts.append(f'{star}성')
        if upgrade:
            parts.append(f'주문서 +{upgrade}')
        grade = item.get('add_grade') or {}
        if grade.get('label'):
            parts.append(f"추옵 {grade['label']}")
        if item.get('potential_grade'):
            parts.append(f"잠재 {item['potential_grade']}")
            for line in item.get('potential') or []:
                parts.append(f'  · {line}')
        if item.get('additional_grade'):
            parts.append(f"에디 {item['additional_grade']}")
            for line in item.get('additional_potential') or []:
                parts.append(f'  · {line}')
        lines.append(' / '.join(parts))
    for slot, item in items.items():
        lines.append(f"{slot or '기타'}: {item['name']}")
    return lines


def weakest_slots(profile, limit=5):
    """추가옵션 등급이 낮은 부위. 무기는 단계 체계가 달라 제외한다.

    0급은 '낮다'가 아니라 '추가옵션이 아예 없다'는 뜻이라 따로 구분해 둔다.
    """
    graded, empty = [], []
    for item in profile.get('equipment') or []:
        grade = (item.get('add_grade') or {}).get('grade')
        if grade is None or item.get('slot') in ('칭호', '안드로이드'):
            continue
        (empty if not grade else graded).append((grade, item['slot'], item['name']))
    graded.sort()
    return ([{'slot': s, 'name': n, 'grade': g, 'empty': False} for g, s, n in graded[:limit]]
            + [{'slot': s, 'name': n, 'grade': 0, 'empty': True} for _, s, n in empty[:limit]])


# 스타포스를 올릴 수 없는 특수 반지(사용자 확인 2026-09-29). 0성이어도 '강화 여지'가 아니다.
NO_STARFORCE_NAMES = {'이터널 플레임 링', '어웨이크 링', '테네브리스 원정대 반지'}
NO_STARFORCE_SLOTS = ('훈장', '뱃지', '칭호', '안드로이드', '포켓 아이템', '엠블렘', '보조무기')


def starforce_possible(item):
    """스타포스를 올릴 수 있는 장비인가.

    시드링(리스트레인트 링 등)은 넥슨 응답의 special_ring_level로, 업그레이드 가능 횟수가 아예 없는 장비
    (정령의 펜던트 등)는 주문서 강화·남은 횟수·복구 가능 횟수가 모두 0인 것으로 안다. 값이 없으면 판단하지 않는다.
    """
    if item.get('slot') in NO_STARFORCE_SLOTS or item.get('name') in NO_STARFORCE_NAMES:
        return False
    if item.get('special_ring_level'):
        return False
    slots = [item.get(k) for k in ('scroll_upgrade', 'upgrade_slots_left', 'upgrade_slots_restorable')]
    if not item.get('starforce') and all(isinstance(v, int) for v in slots) and sum(slots) == 0:
        return False
    return True


def starforce_gaps(profile, limit=5):
    """스타포스가 낮은 부위. 강화 여지가 있는 자리를 모델이 찾기 쉽게 추린다."""
    rows = []
    for item in profile.get('equipment') or []:
        star = item.get('starforce')
        if isinstance(star, int) and starforce_possible(item):
            rows.append((star, item['slot'], item['name']))
    rows.sort()
    return [{'slot': s, 'name': n, 'starforce': st} for st, s, n in rows[:limit]]


def starforce_item(profile, question, topic_item=None):
    """질문이 가리키는 장비 하나. 강화 기대값을 계산할 대상이다.

    순서: 장비 이름을 적었으면 그 장비 → 부위를 적었으면 그 부위(다른 부위를 물을 수 있게) → 장비를 골라 연 대화면 그 장비.
    부위는 긴 이름부터 맞춘다 — '펜던트2'가 든 질문이 '펜던트'(1번)로 잡혀, 장비 대화에서 다른 펜던트를
    계산하던 문제(2026-09-29 사용자 보고: 레벨 120 펜던트로 22성을 계산하려다 실패)."""
    items = [i for i in profile.get('equipment') or [] if i.get('equip_level') and i.get('starforce') is not None]
    named = next((i for i in items if i.get('name') and i['name'] in question), None)
    if named:
        return named
    by_slot = sorted((i for i in items if i.get('slot') and i['slot'] in question), key=lambda i: -len(i['slot']))
    if by_slot:
        return by_slot[0]
    return topic_item


def find_item(profile, topic):
    """대화 주제 장비를 지금 조회한 장비 목록에서 찾는다. 이름이 같으면 우선, 없으면 같은 부위."""
    items = profile.get('equipment') or []
    same = [i for i in items if i.get('name') == topic.get('name')]
    return next((i for i in same if i.get('slot') == topic.get('slot')), None) or \
        (same[0] if same else next((i for i in items if i.get('slot') == topic.get('slot')), None))


def item_summary(item):
    """화면의 장비 카드와 대화 주제에 저장하는 짧은 요약."""
    add = item.get('add_grade') or {}
    return {'slot': item.get('slot'), 'name': item.get('name'), 'icon': item.get('icon'),
            'starforce': item.get('starforce'), 'scroll_upgrade': item.get('scroll_upgrade'),
            'equip_level': item.get('equip_level'), 'add_label': add.get('label') or None,
            'potential_grade': item.get('potential_grade'), 'potential': list(item.get('potential') or []),
            'additional_grade': item.get('additional_grade'),
            'additional_potential': list(item.get('additional_potential') or [])}


def item_text(item):
    """대화 주제 장비 하나의 상세. 모델이 이 장비 이야기를 할 때 근거로 삼는다."""
    add = item.get('add_grade') or {}
    lines = [f"\n[대화 주제 장비] 사용자가 이 장비를 두고 묻고 있다. 부위를 말하지 않으면 이 장비 이야기다.",
             f"- 부위·이름: {item.get('slot')} {item.get('name')}"
             + (f" (장비 레벨 {item['equip_level']})" if item.get('equip_level') else ''),
             f"- 스타포스: {item['starforce']}성" if item.get('starforce') is not None else '- 스타포스: 없음',
             f"- 주문서 강화: {item['scroll_upgrade']}회" if item.get('scroll_upgrade') else '- 주문서 강화: 없음',
             f"- 추가옵션: {add['label']}" if add.get('label') else '- 추가옵션: 없음']
    for label, grade, rows in (('잠재능력', item.get('potential_grade'), item.get('potential')),
                               ('에디셔널 잠재능력', item.get('additional_grade'), item.get('additional_potential'))):
        lines.append(f"- {label}: {grade or '없음'}" + (' / ' + ', '.join(rows) if rows else ''))
    return '\n'.join(lines)


def build(profile, managed=None):
    """모델 프롬프트에 넣을 구조와, 화면에 그대로 보여 줄 수 있는 본문을 함께 돌려준다."""
    stats = stat_map(profile)
    facts = {
        'name': profile.get('name'),
        'level': profile.get('level'),
        'job': profile.get('job'),
        'world': profile.get('world'),
        'guild': profile.get('guild'),
        'combat_power': profile.get('combat_power'),
        'main_stat': profile.get('main_stat'),
        'stats': {k: stats[k] for k in KEY_STATS if k in stats},
        'equipment': equipment_lines(profile),
        'weak_add_options': weakest_slots(profile),
        'low_starforce': starforce_gaps(profile),
        'retrieved_at': profile.get('retrieved_at'),
        'api_date': profile.get('api_date'),
        'equipment_api_date': profile.get('equipment_api_date'),
    }
    if managed:
        facts['goal'] = managed.get('goal') or None
        facts['budget'] = managed.get('budget')
    return facts


def as_text(facts):
    """모델에 넣을 평문. JSON보다 한국어 모델이 덜 흔들린다."""
    lines = [f"캐릭터: {facts.get('name')} / {facts.get('world') or '월드 미확인'} / "
             f"{facts.get('job') or '직업 미확인'} / Lv.{facts.get('level')}"]
    if facts.get('combat_power') is not None:
        lines.append(f"전투력: {facts['combat_power']:,.0f}")
    if facts.get('guild'):
        lines.append(f"길드: {facts['guild']}")
    if facts.get('goal'):
        lines.append(f"사용자가 적어 둔 목표: {facts['goal']}")
    if facts.get('budget'):
        lines.append(f"사용자가 적어 둔 예산: {facts['budget']:,.0f} 메소")
    if facts.get('stats'):
        lines.append('\n[능력치]')
        lines += [f'- {k}: {v}' for k, v in facts['stats'].items()]
    if facts.get('equipment'):
        lines.append('\n[착용 장비]')
        lines += [f'- {line}' for line in facts['equipment']]
    if facts.get('weak_add_options'):
        lines.append('\n[추가옵션 상태] 급 = 주스탯 + 올스탯%x10 + 공/마x4 (커뮤니티 약식 기준). '
                     '급과 스타포스 성은 서로 다른 값이니 섞지 말 것.')
        for r in facts['weak_add_options']:
            note = '추가옵션 없음' if r.get('empty') else f"{r['grade']}급"
            lines.append(f"- {r['slot']}: {note} ({r['name']})")
    if facts.get('low_starforce'):
        lines.append('\n[스타포스가 낮은 부위] 성 = 스타포스 강화 단계. 추가옵션 급과 무관하다.')
        lines += [f"- {r['slot']}: {r['starforce']}성 ({r['name']})" for r in facts['low_starforce']]
    lines.append(f"\n조회 시각: {facts.get('retrieved_at')} / API 기준일: {facts.get('api_date') or '미제공'}")
    return '\n'.join(lines)


def token_estimate(text):
    """한국어는 대략 1.6자당 1토큰으로 잡는다. 문맥 길이를 넘기지 않으려는 용도다."""
    return int(len(text) / 1.6) + 1
