"""강화 조건 슬롯 채우기.

기대값은 조건에 따라 크게 달라진다. 250레벨 18→22성(스페어 45억)이 기본 약 890억인데
샤타포스면 약 438억이다. 그래서 조건을 모른 채 숫자를 내놓지 않는다.

**어떤 조건이 빠졌는지 판단하는 일은 모델에 맡기지 않는다.** 필수 슬롯을 코드로 정의하고
여기서 채운다. 작은 모델은 물어봐야 할 것을 자주 빠뜨리는데, 그러면 틀린 숫자가 그대로 나간다.

한 번 답한 조건은 저장해 두고 다시 묻지 않는다. 이벤트는 주마다 바뀌므로 따로 바꿀 수 있게 한다.
"""
import json
import re

from .core import AppError
from .starforce import DISCOUNTS, EVENTS, SAFEGUARD_STARS as SAFEGUARD_RANGE

SETTING = 'enhance_conditions'
DEFAULTS = {'event': '없음', 'discounts': [], 'safeguard': False, 'use_restore': False}

# 파괴방지는 게임에서 15~17성 시도에만 고를 수 있다. 조건에서는 파괴가 있는 15성 이상 구간을
# 그대로 넘기고, 계산 쪽에서 15~17성만 적용한 뒤 나머지는 '반영하지 않음'으로 알린다.
SAFEGUARD_STARS = list(SAFEGUARD_RANGE)
SAFEGUARD_FROM = SAFEGUARD_STARS[0]

RESET = re.compile(r'(강화\s*)?조건.{0,6}(바꾸|바꿔|바꿀|변경|다시|수정|재설정|초기화)')
_NO = re.compile(r'안\s*[쓰써씀쓸하할함해]|미\s*사용|사용\s*안|못|없|끄|off|아니|제외|빼|x\b', re.I)
# 파괴방지·복구를 가리키는 말. 줄임말(파방)과 '안전모드'라는 말도 받는다.
_WORDS = {'safeguard': re.compile(r'안전\s*모드|파방|파괴\s*방지'),
          'use_restore': re.compile(r'복구')}
# 한 조건에 붙은 말만 보려고 쉼표·마침표나 다른 조건 단어에서 자른다.
_CLAUSE_END = re.compile(r'[,，.·/\n]|안전\s*모드|파방|파괴\s*방지|복구|샤타|이벤트|mvp|pc방|할인', re.I)


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
            # 이벤트 이름 속 '복구' 같은 말이 아래에서 다시 걸리지 않게 지운다.
            body = re.sub(r'\s*'.join(map(re.escape, name.replace(' ', ''))), ' ', body)
            break
    else:
        if '샤타' in body:
            found['event'] = '샤타포스'
            hit = True

    squashed = body.lower().replace(' ', '')
    picked = [name for name in DISCOUNTS if name.lower().replace(' ', '') in squashed]
    if picked:
        found['discounts'] = picked
        hit = True

    # 파괴방지·복구는 언급 자체를 '쓴다'로 보고, 부정 표현이 붙었을 때만 뒤집는다.
    for key, word in _WORDS.items():
        where = word.search(body)
        if not where:
            continue
        hit = True
        rest = body[where.end():where.end() + 18]
        cut = _CLAUSE_END.search(rest)
        found[key] = not _NO.search(rest[:cut.start()] if cut else rest)
    return found if hit else None


def form(current=None):
    """화면에서 버튼으로 고르게 할 선택지. 값은 저장 형식 그대로다."""
    now = {**DEFAULTS, **(current or {})}
    yes_no = lambda value: [{'value': True, 'label': '사용', 'selected': bool(value)},
                            {'value': False, 'label': '미사용', 'selected': not value}]
    return {'kind': 'conditions', 'submit': '이 조건으로 계산', 'questions': [
        {'key': 'event', 'label': '진행 중인 이벤트', 'type': 'single',
         'options': [{'value': name, 'label': '이벤트 없음' if name == '없음' else name,
                      'selected': name == now['event']} for name in EVENTS]},
        {'key': 'safeguard', 'label': '파괴방지 (15~17성에만 적용)', 'type': 'single',
         'options': yes_no(now['safeguard'])},
        {'key': 'use_restore', 'label': '흔적 복구', 'type': 'single',
         'options': yes_no(now['use_restore'])},
        {'key': 'discounts', 'label': '할인 (여러 개 선택 가능)', 'type': 'multi',
         'options': [{'value': name, 'label': name, 'selected': name in now['discounts']}
                     for name in DISCOUNTS]},
    ]}


def from_answer(values):
    """선택창에서 보낸 값을 검사해 저장 형식으로 바꾼다. 목록에 없는 값은 받지 않는다."""
    if not isinstance(values, dict):
        raise AppError('강화 조건 선택값이 올바르지 않습니다.')
    event = values.get('event', '없음')
    discounts = values.get('discounts') or []
    if event not in EVENTS:
        raise AppError('지원하지 않는 이벤트입니다.')
    if not isinstance(discounts, list) or any(not isinstance(d, str) or d not in DISCOUNTS for d in discounts):
        raise AppError('지원하지 않는 할인입니다.')
    if len({d for d in discounts if d.startswith('MVP ')}) > 1:
        raise AppError('MVP 등급은 하나만 골라 주세요. PC방 할인은 함께 쓸 수 있습니다.')
    for key in ('safeguard', 'use_restore'):
        if not isinstance(values.get(key, False), bool):
            raise AppError('파괴방지·흔적 복구는 사용 여부로 골라 주세요.')
    return {'event': event, 'discounts': [d for d in DISCOUNTS if d in discounts],
            'safeguard': bool(values.get('safeguard')), 'use_restore': bool(values.get('use_restore'))}


def ask_text(conditions=None):
    return ('기대값은 강화 조건에 따라 크게 달라져서 먼저 확인합니다. 아래에서 골라 주세요.\n'
            '한 번 고르면 저장해 두고 다시 묻지 않습니다.')


def summary(conditions):
    parts = [f"이벤트 {conditions.get('event') or '없음'}"]
    parts.append('파괴방지 ' + ('사용' if conditions.get('safeguard') else '미사용'))
    parts.append('흔적 복구 ' + ('사용' if conditions.get('use_restore') else '미사용'))
    parts.append('할인 ' + (', '.join(conditions.get('discounts') or []) or '없음'))
    return ' · '.join(parts)


def to_arguments(conditions, current_star, target_star):
    """저장한 조건을 starforce.expected 인자로 바꾼다."""
    return {
        'event': conditions.get('event') or '없음',
        'discounts': list(conditions.get('discounts') or []),
        'use_restore': bool(conditions.get('use_restore')),
        'safeguard': ([s for s in range(max(current_star, SAFEGUARD_FROM), target_star)]
                      if conditions.get('safeguard') else []),
    }
