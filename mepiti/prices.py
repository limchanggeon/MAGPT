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


def resolve(store, item, add_grade=None, job=None, slot=None, group=None):
    """한 장비의 노작값. 모르면 `known=False`로 돌려주고 지어내지 않는다.

    이름으로 못 찾으면 직업군·부위를 써서 '세트 직업군 부위' 값(시세표 격자에서 저장)을 찾는다.
    세트 방어구인데 직업군을 정할 수 없으면(제논, 캐릭터 모름) reason 'group'과 고를 직업군을 돌려준다."""
    saved = store.price_lookup(item, add_grade)
    if saved:
        return _saved_result(item, saved)
    key = set_key(item, job, slot, group)
    saved = store.price_lookup(key, add_grade) if key and key != item else None
    if saved:
        return {**_saved_result(item, saved), 'matched': key}
    # 한도 확인부터 저장까지 묶어 중복 조회와 서로 다른 장비의 한도 경합을 막는다.
    with store.operation('price_fetch'):
        found = _resolve(store, item, add_grade)
    if not found['known'] and set_of(item) and slot_of(item, slot) and not (group or job_group(job)):
        return {**found, 'reason': 'group', 'groups': list(MULTI_ARMOR.get(job, ARMOR_GROUPS))}
    return found


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


def resolve_many(store, items, job=None, group=None):
    """(장비 이름, 추옵 급[, 부위]) 목록을 한 번에. 같은 장비는 한 번만 본다."""
    seen, results = set(), []
    for item, grade, *rest in items:
        if not item or item in seen:
            continue
        seen.add(item)
        results.append(resolve(store, item, grade, job, rest[0] if rest else None, group))
    return results


def status(store):
    return {'stored': store.price_count(), 'fetcher': fetcher_available(),
            'fetch_enabled': fetch_enabled(store), 'daily_limit': daily_limit(store),
            'used_today': _used_today(store) if fetcher_available() else 0}


def ask_group(unknown, job=None):
    """직업군을 정할 수 없는 세트 방어구가 있으면 되물을 (문구, 버튼, 안내). 없으면 None."""
    need = [u for u in unknown if u.get('reason') == 'group']
    if not need:
        return None
    names = ', '.join(u['item'] for u in need[:3])
    note = (f"{job}은(는) 도적·해적 방어구를 모두 낄 수 있어 어느 쪽인지 정할 수 없어요." if job in MULTI_ARMOR
            else '캐릭터 직업을 몰라 어느 직업군 방어구인지 정할 수 없어요.')
    return (f'{names}은(는) 직업군마다 다른 장비예요. 어느 직업군 방어구의 노작값을 볼까요?',
            [{'label': f'{g} 방어구', 'reply': f'{g} 방어구'} for g in need[0]['groups']], note)


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
# 부위×직업 격자표(커뮤니티 방어구 시세표)는 세트 이름 없이 '전사 상의'로 읽힌다. 세트를 모르는 채 저장하면
# 어느 세트의 값인지 알 수 없으므로 세트 이름을 붙여야 저장한다(표의 글자나 모델이 아이콘·가격대로 추론한 세트, 또는 화면에서 고른 세트).
GRID_JOBS = ('전사', '법사', '마법사', '궁수', '도적', '해적')
GRID_SLOTS = {'모자': '모자', '상의': '상의', '하의': '하의', '견장': '어깨장식', '어깨': '어깨장식', '어깨장식': '어깨장식',
              '장갑': '장갑', '신발': '신발', '망토': '망토'}
_GRID = re.compile(r'^\s*(' + '|'.join(GRID_JOBS) + r')\s*(' + '|'.join(sorted(GRID_SLOTS, key=len, reverse=True)) + r')\s*$')


def grid_name(item):
    """세트 이름 없는 격자 칸 이름('전사 견장', '법사 모자')이면 '전사 어깨장식', '마법사 모자'로 고쳐 돌려준다. 아니면 None."""
    m = _GRID.match(str(item or ''))
    return f"{'마법사' if m.group(1) == '법사' else m.group(1)} {GRID_SLOTS[m.group(2)]}" if m else None


# 방어구 세트 노작값은 '세트 직업군 부위'(예: '에테르넬 전사 모자')로 저장된다(시세표 격자). 실제 장비 이름이나
# 줄임말('에테뚝')로 물어도 세트·부위를 뽑고 캐릭터 직업을 직업군으로 바꿔 그 값을 찾는다.
SET_ALIASES = (('에테르넬', '에테르넬'), ('에테', '에테르넬'), ('아케인셰이드', '아케인셰이드'), ('아케인', '아케인셰이드'),
               ('앱솔랩스', '앱솔랩스'), ('앱솔', '앱솔랩스'), ('루타비스', '루타비스'), ('루타', '루타비스'))
SLOT_WORDS = {**GRID_SLOTS, '뚝배기': '모자', '뚝': '모자'}
_SET = '|'.join(a for a, _ in SET_ALIASES)
_SLOT = '|'.join(sorted(SLOT_WORDS, key=len, reverse=True))
_NAMED = re.compile(r'(' + _SET + r')\s*(상하의|' + _SLOT + r')')


def set_of(name):
    name = str(name or '').strip()
    return next((full for alias, full in SET_ALIASES if name.startswith(alias)), None)


ARMOR_GROUPS = ('전사', '마법사', '궁수', '도적', '해적')
# 방어구 직업군이 주스탯 계열(peers.JOB_FAMILIES)과 다른 직업. 제논은 한 직업군으로 정할 수 없어 묻는다.
ARMOR_JOBS = {'데몬어벤져': '전사'}
MULTI_ARMOR = {'제논': ('도적', '해적')}
_GROUP_WORD = re.compile(r'(전사|마법사|법사|궁수|도적|해적)')


def group_in(text):
    """질문에 직업군 방어구를 말했으면('도적 에테뚝', '해적 방어구') 그 직업군. 캐릭터 직업보다 앞선다."""
    m = _GROUP_WORD.search(text or '')
    return ('마법사' if m.group(1) == '법사' else m.group(1)) if m else None


def job_group(job):
    """직업 → 방어구 직업군('렌' → '전사', '데몬어벤져' → '전사'). 제논·모르는 직업은 None."""
    if job in ARMOR_JOBS:
        return ARMOR_JOBS[job]
    from .peers import family_of          # peers가 무거워 필요할 때만 불러온다
    label, _ = family_of(job or '')
    return label.split('·')[0] if label else None


def slot_of(item, slot=None):
    return SLOT_WORDS.get(slot or '') or next((SLOT_WORDS[w] for w in sorted(SLOT_WORDS, key=len, reverse=True)
                                              if str(item or '').endswith(w)), None)


def set_key(item, job=None, slot=None, group=None):
    """'에테르넬 모자'·실제 장비 이름 + 직업군(말한 것, 없으면 캐릭터 직업)·부위 → '에테르넬 전사 모자'. 하나라도 모르면 None."""
    family, group, slot = set_of(item), group or job_group(job), slot_of(item, slot)
    return f'{family} {group} {slot}' if family and group and slot else None


def named_targets(question):
    """질문에 나온 세트 방어구('에테뚝', '아케인 상하의') → [(이름, 급, 부위)]."""
    out = []
    for alias, word in _NAMED.findall(question or ''):
        family = dict(SET_ALIASES)[alias]
        for slot in (('상의', '하의') if word == '상하의' else (SLOT_WORDS[word],)):
            if (f'{family} {slot}', None, slot) not in out:
                out.append((f'{family} {slot}', None, slot))
    return out


def read_table(model, selected, image):
    """시세표 이미지(data URL)에서 아이템 이름·가격을 읽어 메소로 바꾼다. 저장하지 않는다 — 사용자가 확인하고 고른 것만 저장한다.

    단위 없는 숫자는 표 머리의 단위(억·만)로, 머리에 단위가 없으면 억으로 읽는다('19.56' → 19억 5600만, '90만'은 그대로)."""
    head, _, data = str(image or '').partition(',')
    mime = head[5:].split(';')[0] if head.startswith('data:') else ''
    if mime not in TABLE_TYPES or ';base64' not in head or not data:
        raise AppError('PNG·JPG·WEBP 시세표 이미지를 넣어 주세요.')
    raw, meta = model.read_price_table(selected, mime, data)
    unit = '만' if (raw.get('unit') or '').strip().startswith('만') else '억'
    set_name = (raw.get('set') or '').strip() or None
    rows = []
    for r in raw['rows']:
        price = parse_price(r['price'], unit)
        row = {'item': r['item'], 'price_text': r['price'], 'price': round(price) if price and price > 0 else None}   # 166.67억 같은 소수 오차는 메소 단위로 반올림
        grid = grid_name(r['item'])
        if grid:
            row.update(item=f'{set_name} {grid}' if set_name else grid, grid=grid)
        rows.append(row)
    return {'unit': raw.get('unit'), 'server': raw.get('server'), 'set': set_name,
            'set_basis': (raw.get('set_basis') or 'text') if set_name else None, 'rows': rows,
            'model': meta.get('model') if isinstance(meta, dict) else None}


def save_many(store, rows, note=None):
    """시세표에서 고른 값들을 한 번에 저장(출처 '시세표 이미지')."""
    saved, unnamed = [], 0
    for r in rows[:200]:
        price = r.get('price')
        if isinstance(price, str):
            price = parse_price(price, '억')
        if not r.get('item') or not price:
            continue
        if grid_name(r['item']):          # 세트 이름 없는 '전사 상의'는 어느 세트인지 몰라 저장하지 않는다
            unnamed += 1
            continue
        saved.append(store.price_save({'item': str(r['item'])[:100], 'price': price, 'source': '시세표 이미지',
                                       'note': (note or '')[:300] or None}))
    return {'saved': len(saved), 'unnamed': unnamed}
