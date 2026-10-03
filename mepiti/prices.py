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
    return store.price_fetch_count(now()[:10])


def resolve(store, item, add_grade=None):
    """한 장비의 노작값. 모르면 `known=False`로 돌려주고 지어내지 않는다."""
    saved = store.price_lookup(item, add_grade)
    if saved:
        return _saved_result(item, saved)
    # 한도 확인부터 저장까지 묶어 중복 조회와 서로 다른 장비의 한도 경합을 막는다.
    with store.operation('price_fetch'):
        return _resolve(store, item, add_grade)


def _resolve(store, item, add_grade):
    saved = store.price_lookup(item, add_grade)
    if saved:
        return _saved_result(item, saved)
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


def _saved_result(item, saved):
    return {'item': item, 'known': True, 'price': saved['price'], 'source': saved['source'],
            'recorded_at': saved['recorded_at'], 'add_grade': saved['add_grade']}


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


DEFAULT_SCALES = {'억': 1_0000_0000, '만': 1_0000}


def parse_price(text, default_unit=None):
    """'32억', '1조 2000억', '3,000만', '2천만', '1억 5천만', '25000000000' 형태를 메소 숫자로.

    '메소', '정도' 같은 말이 붙어 있어도 금액 부분만 읽는다. 금액이 없으면 None.
    default_unit('억'·'만')을 주면 단위 없이 적은 작은 수(10만 미만)를 그 단위로 읽는다: 12.5 → 12억 5천만(사용자 입력칸).
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
        value = float(plain[0])
        if default_unit in DEFAULT_SCALES and value < 100000 and re.fullmatch(r'\s*[\d.]+\s*(?:메소)?\s*', cleaned):
            value *= DEFAULT_SCALES[default_unit]
        return value
    return None


TABLE_TYPES = ('image/png', 'image/jpeg', 'image/webp')


def read_table(model, selected, image):
    """시세표 이미지(data URL)에서 아이템 이름·가격을 읽어 메소로 바꾼다. 저장하지 않는다 — 사용자가 확인하고 고른 것만 저장한다.

    단위 없는 숫자는 표 머리의 단위(억·만)로, 머리에 단위가 없으면 억으로 읽는다('19.56' → 19억 5600만, '90만'은 그대로)."""
    head, _, data = str(image or '').partition(',')
    mime = head[5:].split(';')[0] if head.startswith('data:') else ''
    if mime not in TABLE_TYPES or ';base64' not in head or not data:
        raise AppError('PNG·JPG·WEBP 시세표 이미지를 넣어 주세요.')
    raw, meta = model.read_price_table(selected, mime, data)
    unit = '만' if (raw.get('unit') or '').strip().startswith('만') else '억'
    rows = []
    for r in raw['rows']:
        price = parse_price(r['price'], unit)
        rows.append({'item': r['item'], 'price_text': r['price'], 'price': round(price) if price and price > 0 else None})   # 166.67억 같은 소수 오차는 메소 단위로 반올림
    return {'unit': raw.get('unit'), 'server': raw.get('server'), 'rows': rows,
            'model': meta.get('model') if isinstance(meta, dict) else None}


def save_many(store, rows, note=None):
    """시세표에서 고른 값들을 한 번에 저장(출처 '시세표 이미지')."""
    saved = []
    for r in rows[:200]:
        price = r.get('price')
        if isinstance(price, str):
            price = parse_price(price, '억')
        if not r.get('item') or not price:
            continue
        saved.append(store.price_save({'item': str(r['item'])[:100], 'price': price, 'source': '시세표 이미지',
                                       'note': (note or '')[:300] or None}))
    return {'saved': len(saved)}
