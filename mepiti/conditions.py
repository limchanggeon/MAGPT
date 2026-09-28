"""강화 조건 슬롯 채우기.

기대값은 조건에 따라 몇 배씩 달라진다. 250레벨 18→22성이 기본 88.9조인데
샤타포스면 43.8조, 안전모드를 쓰면 15.5조다. 그래서 조건을 모른 채 숫자를 내놓지 않는다.

**어떤 조건이 빠졌는지 판단하는 일은 모델에 맡기지 않는다.** 필수 슬롯을 코드로 정의하고
여기서 채운다. 작은 모델은 물어봐야 할 것을 자주 빠뜨리는데, 그러면 틀린 숫자가 그대로 나간다.

한 번 답한 조건은 저장해 두고 다시 묻지 않는다. 이벤트는 주마다 바뀌므로 따로 바꿀 수 있게 한다.
"""
import json
import re

from .starforce import DISCOUNTS, EVENTS

SETTING = 'enhance_conditions'
DEFAULTS = {'event': '없음', 'discounts': [], 'safeguard': False, 'use_restore': False}

# 안전모드는 파괴가 생기는 구간에만 의미가 있다.
SAFEGUARD_STARS = list(range(15, 22))

RESET = re.compile(r'(강화\s*)?조건.{0,6}(바꾸|바꿔|바꿀|변경|다시|수정|재설정|초기화)')
_NO = re.compile(r'안\s*[쓰써씀쓸]|미\s*사용|사용\s*안|없|끄|off|아니|제외|빼')


def load(store):
    try:
        saved = json.loads(store.setting(SETTING) or '{}')
    except ValueError:
        saved = {}
    conditions = dict(DEFAULTS)
    if isinstance(saved, dict):
        if saved.get('event') in EVENTS:
            conditions['event'] = saved['event']
        conditions['discounts'] = [d for d in (saved.get('discounts') or []) if d in DISCOUNTS]
        conditions['safeguard'] = bool(saved.get('safeguard'))
        conditions['use_restore'] = bool(saved.get('use_restore'))
        conditions['answered'] = bool(saved.get('answered'))
    return conditions


def save(store, conditions):
    store.set_setting(SETTING, json.dumps({**conditions, 'answered': True}, ensure_ascii=False))


def answered(store):
    return bool(load(store).get('answered'))


def clear(store):
    store.set_setting(SETTING, '')


def parse(text):
    """자유롭게 쓴 답에서 조건을 읽는다.

    **말한 항목만** 돌려준다. 후속 질문에서 한 가지만 바꿀 때 나머지가 초기화되면 안 되기 때문이다.
    '기본'이라고만 하면 전체를 기본값으로 되돌린다는 뜻이라 DEFAULTS 전체를 돌려준다.
    읽어낸 게 없으면 None.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    body = text.strip()
    found = {}
    hit = False

    if re.search(r'기본|그냥|없음|아무것도|디폴트', body) and not re.search(r'이벤트\s*\S', body):
        found.update(DEFAULTS)
        hit = True

    for name in sorted(EVENTS, key=len, reverse=True):
        if name != '없음' and name.replace(' ', '') in body.replace(' ', ''):
            found['event'] = name
            hit = True
            break
    else:
        if '샤타' in body:
            found['event'] = '샤타포스'
            hit = True

    picked = [name for name in DISCOUNTS if name.replace(' ', '') in body.replace(' ', '')]
    if 'pc방' in body.lower().replace(' ', '') and 'PC방' not in picked:
        picked.append('PC방')
    if picked:
        found['discounts'] = picked
        hit = True

    # 안전모드·복구는 언급 자체를 '쓴다'로 보고, 부정 표현이 붙었을 때만 뒤집는다.
    for key, word in (('safeguard', '안전모드'), ('use_restore', '복구')):
        where = body.find(word)
        if where < 0:
            continue
        hit = True
        found[key] = not _NO.search(body[where:where + 18])
    return found if hit else None


def ask_text(conditions=None):
    events = ' / '.join(name for name in EVENTS if name != '없음')
    return ('기대값은 조건에 따라 크게 달라져서 먼저 확인합니다. 한 번 답하면 저장해 두고 다시 묻지 않습니다.\n\n'
            f'1. 진행 중인 이벤트 — {events}\n'
            '2. 안전모드(파괴 방지) 사용 여부\n'
            '3. 흔적 복구 사용 여부\n'
            f"4. 할인 — {' / '.join(DISCOUNTS)}\n\n"
            '해당 없으면 "기본"이라고만 적어 주세요. 예: 샤타포스, 안전모드 사용, MVP 다이아')


def summary(conditions):
    parts = [f"이벤트 {conditions.get('event') or '없음'}"]
    parts.append('안전모드 ' + ('사용' if conditions.get('safeguard') else '미사용'))
    parts.append('흔적 복구 ' + ('사용' if conditions.get('use_restore') else '미사용'))
    parts.append('할인 ' + (', '.join(conditions.get('discounts') or []) or '없음'))
    return ' · '.join(parts)


def to_arguments(conditions, current_star, target_star):
    """저장한 조건을 starforce.expected 인자로 바꾼다."""
    return {
        'event': conditions.get('event') or '없음',
        'discounts': list(conditions.get('discounts') or []),
        'use_restore': bool(conditions.get('use_restore')),
        'safeguard': ([s for s in SAFEGUARD_STARS if current_star <= s < target_star]
                      if conditions.get('safeguard') else []),
    }
