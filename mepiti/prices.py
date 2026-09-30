"""장비 노작값(작업하지 않은 상태의 시세) 확보.

값을 얻는 순서는 셋이다.
  1. 저장된 값 — 한 번 알아낸 값은 계속 쓴다. 조회 횟수를 아끼는 것이 목적이다.
  2. 외부 조회기 — 등록되어 있고 켜져 있을 때만 부른다. 앱 창으로 실행하면 경매장(mepiti/auction.py)이 붙는다.
  3. 사용자에게 되묻기 — 위 둘이 실패하면 모르는 채로 답하지 않고 값을 물어본다.

값이 충분히 쌓이면 설정에서 꺼서 더 이상 조회하지 않게 할 수 있다.
"""
import re

from .core import AppError, now

FETCH_SETTING = 'price_fetch'          # '1'이면 외부 조회를 시도한다.
DAILY_LIMIT_SETTING = 'price_fetch_limit'
DEFAULT_DAILY_LIMIT = 100              # 경매장 MCP의 일일 검색 제한에 맞춘 기본값.

_fetcher = None


def register_fetcher(callable_or_none):
    """외부 조회기를 등록한다. `fetch(item, add_grade) -> {'price':..., 'note':...} | None`."""
    global _fetcher
    _fetcher = callable_or_none


def fetcher_available():
    return _fetcher is not None


def fetch_enabled(store):
    return fetcher_available() and store.setting(FETCH_SETTING) == '1'


def daily_limit(store):
    try:
        return int(store.setting(DAILY_LIMIT_SETTING) or DEFAULT_DAILY_LIMIT)
    except ValueError:
        return DEFAULT_DAILY_LIMIT


def _used_today(store):
    today = now()[:10]
    return len([p for p in store.prices(1000)
                if p['source'] != 'user' and str(p['recorded_at'])[:10] == today])


def resolve(store, item, add_grade=None):
    """한 장비의 노작값. 모르면 `known=False`로 돌려주고 지어내지 않는다."""
    saved = store.price_lookup(item, add_grade)
    if saved:
        return {'item': item, 'known': True, 'price': saved['price'], 'source': saved['source'],
                'recorded_at': saved['recorded_at'], 'add_grade': saved['add_grade']}
    if fetch_enabled(store):
        if _used_today(store) >= daily_limit(store):
            return {'item': item, 'known': False, 'reason': 'limit'}
        try:
            found = _fetcher(item, add_grade)
        except AppError as e:
            # 경매장 로그인이 풀린 경우 등. 이유를 되묻기 문구에 함께 보여 준다.
            return {'item': item, 'known': False, 'reason': 'fetch_error', 'error': str(e)}
        except Exception:
            found = None
        if found and found.get('price'):
            record = store.price_save({'item': item, 'add_grade': add_grade,
                                       'price': found['price'], 'source': found.get('source', 'auction'),
                                       'note': found.get('note')})
            return {'item': item, 'known': True, 'price': record['price'],
                    'source': record['source'], 'recorded_at': record['recorded_at'],
                    'add_grade': add_grade}
    return {'item': item, 'known': False,
            'reason': 'disabled' if not fetcher_available() else 'missing'}


def resolve_many(store, items):
    """(장비 이름, 추옵 급) 목록을 한 번에. 같은 장비는 한 번만 본다."""
    seen, results = set(), []
    for item, grade in items:
        if not item or item in seen:
            continue
        seen.add(item)
        results.append(resolve(store, item, grade))
    return results


def status(store):
    return {'stored': store.price_count(), 'fetcher': fetcher_available(),
            'fetch_enabled': fetch_enabled(store), 'daily_limit': daily_limit(store),
            'used_today': _used_today(store) if fetcher_available() else 0}


def ask_text(unknown):
    """모르는 값이 있을 때 사용자에게 보낼 되묻기 문구."""
    if not unknown:
        return ''
    limited = any(u.get('reason') == 'limit' for u in unknown)
    head = ('오늘 조회 한도를 다 써서 아래 장비의 노작값을 가져오지 못했습니다.'
            if limited else '아래 장비의 노작값을 알지 못합니다.')
    failed = next((u['error'] for u in unknown if u.get('reason') == 'fetch_error'), None)
    if failed:
        head += f' (경매장 조회 실패: {failed})'
    return f'{head} 값을 알려주시면 저장해 두고 다음부터는 묻지 않습니다.'


def form(unknown, skippable=False):
    """화면에서 장비별로 값을 적게 할 입력칸.

    `skippable`이면 값을 몰라도 넘어갈 수 있다(시세 비교용). 강화 기대값의 스페어 값처럼
    계산에 꼭 필요한 값은 건너뛸 수 없다.
    """
    return {'kind': 'price', 'submit': '저장하고 계속', 'skippable': bool(skippable),
            'fields': [{'item': u['item'], 'placeholder': '예: 2천만, 32억'} for u in unknown[:8]]}


# 사용자가 답으로 적어 주는 금액 표기를 메소로 바꾼다.
UNITS = (('조', 1_0000_0000_0000), ('억', 1_0000_0000), ('만', 1_0000))
_THOUSAND = re.compile(r'(\d+(?:\.\d+)?)\s*천')


def parse_price(text):
    """'32억', '1조 2000억', '3,000만', '2천만', '1억 5천만', '25000000000' 형태를 메소 숫자로.

    '메소', '정도' 같은 말이 붙어 있어도 금액 부분만 읽는다. 금액이 없으면 None.
    """
    if not isinstance(text, str):
        return None
    cleaned = text.replace(',', '').strip()
    # '2천만' -> '2000만', '5천' -> '5000'
    cleaned = _THOUSAND.sub(lambda m: format(float(m.group(1)) * 1000, 'g'), cleaned)
    total, matched = 0.0, False
    for unit, scale in UNITS:
        found = re.search(r'(\d+(?:\.\d+)?)\s*' + unit, cleaned)
        if found:
            total += float(found.group(1)) * scale
            matched = True
            cleaned = cleaned[:found.start()] + ' ' + cleaned[found.end():]
    plain = re.findall(r'\d+(?:\.\d+)?', cleaned)
    if matched:
        # '2764만 4807'처럼 만 아래 자리가 붙은 표기(게임 화면의 메소 표시). 남은 숫자가 하나이고 1만보다 작으면 더한다.
        if len(plain) == 1 and float(plain[0]) < 10000:
            total += float(plain[0])
        return total or None
    if len(plain) == 1 and float(plain[0]) > 0:
        return float(plain[0])
    return None
