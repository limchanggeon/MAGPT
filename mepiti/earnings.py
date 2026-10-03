"""수익 기록 — 재획(재물 획득의 비약 사냥)과 주간 보스.

재획: 번 메소 + 솔 에르다 조각 개수 x 조각 가격 = 이번 재획 총수익.
주보: 결정석 판매가 / 파티 인원 + 추가 드롭 수익 = 내 몫.
주간 합계는 게임의 주간 초기화(목요일 0시, KST) 기준으로 묶는다.

가격·수익은 모두 사용자가 적은 값이다. 앱이 시세를 지어내지 않는다.
"""
import re
from datetime import date, datetime, timedelta

from .core import AppError, KST, identifier, now
from .prices import parse_price

PIECE_PRICE = 'piece_price'      # 마지막으로 쓴 조각 가격. 다음 기록의 기본값이다.
BOSS_PRICES = 'boss_prices'      # 보스별 마지막 결정석 판매가. 스케줄러로 불러올 때 채운다.
MAX_MESO = 10_000_0000_0000      # 1조. 오타로 자릿수가 크게 넘어가는 것만 막는다.
PIECE_ITEM = '솔 에르다 조각'
PIECE_AUCTION = 'piece_price_auction'   # 경매장에서 본 조각 시세 {price, world, at}. 한 시간 안에는 다시 검색하지 않는다
PIECE_FRESH_MINUTES = 60
MIN_PIECE_PRICE = 10_000          # 솔 에르다 조각 1개가 1만 메소보다 쌀 수는 없다. 단위를 빼먹은 입력('650')을 잡는다.

SCHEMA = '''
CREATE TABLE IF NOT EXISTS earnings(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, day TEXT NOT NULL,
  meso REAL, pieces INTEGER, piece_price REAL, flasks REAL,
  boss TEXT, crystal REAL, party INTEGER, extra REAL,
  note TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS earnings_day ON earnings(day);
'''


def ensure(store):
    with store.schema('earnings') as needed:
        if not needed:
            return
        with store.db() as db:
            db.executescript(SCHEMA)
            # 스케줄러로 불러온 기록의 캐릭터와 중복 방지 키. 예전 표에는 없어 추가만 한다.
            columns = [r['name'] for r in db.execute('PRAGMA table_info(earnings)')]
            for column in ('character', 'source_key'):
                if column not in columns:
                    db.execute(f'ALTER TABLE earnings ADD COLUMN {column} TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS earnings_source ON earnings(source_key)')


def today():
    return datetime.now(KST).date()


def week_start(day):
    """그 날짜가 속한 주의 목요일(주간 보스 초기화일)."""
    return day - timedelta(days=(day.weekday() - 3) % 7)


def meso(value, label, required=False, unit='억'):
    """'12억', '3,500만', '2천만', 숫자를 메소로. 빈칸이면 0(필수면 오류). 단위 없이 적은 작은 수는 unit(기본 억)으로 읽는다(12.5 → 12억 5천만)."""
    if value is None or str(value).strip() == '':
        if required:
            raise AppError(f'{label}을(를) 입력해 주세요.')
        return 0.0
    if isinstance(value, bool):
        raise AppError(f'{label} 값을 확인해 주세요.')
    if isinstance(value, (int, float)):
        amount = float(value)
    elif re.fullmatch(r'\s*0+(?:\.0+)?\s*', str(value)):
        amount = 0.0
    else:
        amount = parse_price(str(value), unit)
        if amount is None:
            raise AppError(f"{label}을(를) 읽지 못했습니다. '12.5'(억 단위)나 '3500만'처럼 적어 주세요.")
    if not 0 <= amount <= MAX_MESO or amount != amount:
        raise AppError(f'{label}은(는) 0 이상이어야 합니다.')
    return amount


def count(value, label, low, high, default=None):
    if value is None or str(value).strip() == '':
        if default is None:
            raise AppError(f'{label}을(를) 입력해 주세요.')
        return default
    try:
        number = float(str(value).replace(',', ''))
    except ValueError:
        raise AppError(f'{label}은(는) 숫자로 적어 주세요.')
    if not low <= number <= high:
        raise AppError(f'{label}은(는) {low}~{high} 사이여야 합니다.')
    return number


def parse_day(value):
    if not value:
        return today()
    try:
        day = date.fromisoformat(str(value))
    except ValueError:
        raise AppError('날짜는 YYYY-MM-DD 형식이어야 합니다.')
    if day > today() + timedelta(days=1):
        raise AppError('미래 날짜는 기록할 수 없습니다.')
    return day


def total(row):
    """기록 한 줄의 수익. 재획은 메소+조각, 주보는 내 몫."""
    if row['kind'] == 'hunt':
        return (row['meso'] or 0) + (row['pieces'] or 0) * (row['piece_price'] or 0)
    return (row['crystal'] or 0) / max(1, row['party'] or 1) + (row['extra'] or 0)


def add(store, data):
    ensure(store)
    kind = data.get('kind')
    day = parse_day(data.get('day'))
    note = str(data.get('note') or '').strip()[:200] or None
    row = {'id': identifier(), 'kind': kind, 'day': day.isoformat(), 'meso': None, 'pieces': None,
           'piece_price': None, 'flasks': None, 'boss': None, 'crystal': None, 'party': None,
           'extra': None, 'note': note, 'created_at': now(),
           'character': str(data.get('character') or '').strip()[:30] or None,
           'source_key': str(data.get('source_key') or '').strip()[:120] or None}
    if kind == 'hunt':
        row['meso'] = meso(data.get('meso'), '번 메소')
        row['pieces'] = int(count(data.get('pieces'), '조각 개수', 0, 100000, 0))
        row['piece_price'] = meso(data.get('piece_price'), '조각 가격', unit='만')     # 조각은 수백만 — 단위 없이 '650'이면 650만
        flasks = count(data.get('flasks'), '소재비 개수', 0, 100, 0)
        row['flasks'] = flasks or None
        if not row['meso'] and not row['pieces']:
            raise AppError('번 메소나 조각 개수 중 하나는 적어 주세요.')
        if row['pieces'] and not row['piece_price']:
            raise AppError('조각 가격을 적어야 총수익을 계산할 수 있습니다.')
        if row['piece_price'] and row['piece_price'] < MIN_PIECE_PRICE:
            # '650'처럼 단위 없이 적으면 650메소가 되어 조각이 수익에 거의 안 들어간다(사용자 보고 2026-09-29).
            raise AppError(f"조각 가격이 {row['piece_price']:,.0f}메소로 읽혔어요. 1개 가격을 '650만'처럼 단위를 붙여 적어 주세요.")
        if row['piece_price']:
            store.set_setting(PIECE_PRICE, row['piece_price'])
    elif kind == 'boss':
        boss = str(data.get('boss') or '').strip()
        if not boss or len(boss) > 30:
            raise AppError('보스 이름을 30자 이내로 적어 주세요.')
        row['boss'] = boss
        row['crystal'] = meso(data.get('crystal'), '결정석 판매가')
        if not row['crystal'] and not str(data.get('crystal') or '').strip():
            # 결정석 가격을 비워 두면 공식 공지 가격표에서 채운다.
            row['crystal'] = crystal_for(store, row['character'], *parse_label(boss), day.isoformat()) or 0.0
        row['party'] = int(count(data.get('party'), '파티 인원', 1, 6, 1))
        row['extra'] = meso(data.get('extra'), '추가 드롭 수익')
        if not row['crystal'] and not row['extra']:
            raise AppError('결정석 판매가나 추가 드롭 수익 중 하나는 적어 주세요.')
        if counts_toward_limit(boss):
            # 주간 보스 결정석은 캐릭터마다 주 12개까지(검은 마법사는 월간이라 빼고 센다). 2026-10-03 사용자 확인.
            start = week_start(day)
            used = weekly_boss_count(store, row['character'], start)
            if used >= WEEKLY_BOSS_LIMIT:
                who = row['character'] or '캐릭터 미지정'
                raise AppError(f"{who}은(는) {start.isoformat()} 주에 주간 보스를 이미 {WEEKLY_BOSS_LIMIT}개 기록했어요"
                               f"(캐릭터당 주 {WEEKLY_BOSS_LIMIT}개, 검은 마법사 제외).", 409)
        if not counts_toward_limit(boss) and boss.startswith(MONTHLY_BOSSES) and monthly_boss_done(store, row['character'], day):
            # 검은 마법사는 한 달에 한 번(난이도 상관없이). 2026-10-04 사용자: "검마도 한달에 한번인데 중복으로 가능하네".
            who = row['character'] or '캐릭터 미지정'
            raise AppError(f"{who}은(는) {day.isoformat()[:7]}에 검은 마법사를 이미 기록했어요(한 달에 한 번).", 409)
        if row['source_key'] and store.rows('SELECT 1 FROM earnings WHERE source_key=?', (row['source_key'],)):
            raise AppError(f"{row['character'] or ''} {boss}은(는) 이번 주에 이미 기록했습니다.".strip(), 409)
        if row['crystal'] and row['crystal'] != crystal_for(store, row['character'], *parse_label(boss), day.isoformat()):
            # 공식 가격과 다른 값을 적었을 때만 기억한다(표에 없는 보스 등).
            remembered = store.setting(BOSS_PRICES) or {}
            remembered[boss] = row['crystal']
            store.set_setting(BOSS_PRICES, remembered)
    else:
        raise AppError('수익 종류는 재획 또는 주보만 기록할 수 있습니다.')
    with store.db() as db:
        db.execute('INSERT INTO earnings(id,kind,day,meso,pieces,piece_price,flasks,boss,crystal,party,extra,'
                   'note,created_at,character,source_key) VALUES(:id,:kind,:day,:meso,:pieces,:piece_price,:flasks,'
                   ':boss,:crystal,:party,:extra,:note,:created_at,:character,:source_key)', row)
    return {**row, 'total': total(row)}


WEEKLY_BOSS_LIMIT = 12
MONTHLY_BOSSES = ('검은 마법사',)
NOT_BOSSES = ('추가 드롭',)


def counts_toward_limit(boss):
    """주 12개 제한에 들어가는 보스인가(검은 마법사·'추가 드롭' 줄은 빼고)."""
    name = str(boss or '')
    return not name.startswith(MONTHLY_BOSSES) and name not in NOT_BOSSES


def monthly_boss_done(store, character, day):
    """그 캐릭터가 그 달(1일 초기화)에 월간 보스(검은 마법사)를 이미 기록했나."""
    first = day.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    rows = store.rows('SELECT boss FROM earnings WHERE kind=? AND day BETWEEN ? AND ? AND character IS ?',
                      ('boss', first.isoformat(), last.isoformat(), character))
    return any(str(r['boss'] or '').startswith(MONTHLY_BOSSES) for r in rows)


def weekly_boss_count(store, character, start):
    rows = store.rows('SELECT boss FROM earnings WHERE kind=? AND day BETWEEN ? AND ? AND character IS ?',
                      ('boss', start.isoformat(), (start + timedelta(days=6)).isoformat(), character))
    return sum(1 for r in rows if counts_toward_limit(r['boss']))


def piece_price(store, auction, refresh=False):
    """솔 에르다 조각 시세(경매장, 검색 기준 캐릭터 월드의 판매 중 최저 개당 가격). 한 시간 안에 본 값은 다시 쓴다(하루 검색 100회 아끼기)."""
    cached = store.setting(PIECE_AUCTION) or None
    if cached and not refresh:
        try:
            age = datetime.now(KST) - datetime.fromisoformat(cached['at'])
            if age < timedelta(minutes=PIECE_FRESH_MINUTES):
                return {**cached, 'cached': True}
        except (KeyError, ValueError, TypeError):
            pass
    if auction is None:
        raise AppError('경매장 시세는 메피티를 앱 창으로 실행하고 설정 → 장비 노작값에서 경매장에 연결했을 때 쓸 수 있어요.', 409)
    found = auction.world_price(PIECE_ITEM)
    store.set_setting(PIECE_AUCTION, found)
    return {**found, 'cached': False}


def delete(store, record_id):
    ensure(store)
    with store.db() as db:
        if not db.execute('DELETE FROM earnings WHERE id=?', (str(record_id),)).rowcount:
            raise AppError('기록을 찾을 수 없습니다.', 404)
    return {'ok': True}


NO_CHARACTER = '미지정'     # 캐릭터를 고르지 않은 기록(예전 재획 기록 등)
ACCOUNT_CHARACTERS = 'account_characters'   # 넥슨 계정 캐릭터 목록(캐릭터 목록 불러오기 때 저장)


def character_choices(store, rows):
    """수익 기록에서 고를 캐릭터: 관리 중(대표 먼저) → 계정 캐릭터(레벨 높은 순) → 예전 기록에만 있는 이름."""
    managed = store.characters(include_snapshots=False)
    account = sorted(store.setting(ACCOUNT_CHARACTERS) or [], key=lambda c: -(c.get('level') or 0))
    seen, out = set(), []
    for group, items in (('관리 중', managed), ('계정', account)):
        for c in items:
            name = c.get('name')
            if name and name not in seen:
                seen.add(name)
                out.append({'name': name, 'group': group, 'world': c.get('world'), 'level': c.get('level')})
    for name in sorted({r['character'] for r in rows if r.get('character')} - seen):
        out.append({'name': name, 'group': '기록', 'world': None, 'level': None})
    default = next((c['name'] for c in managed if c.get('main')), None) or (managed[0]['name'] if managed else None)
    return out, default


def parse_week(value):
    """보려는 주. 아무 날짜나 받아 그 주의 목요일로 맞춘다. 없으면 이번 주."""
    if not value:
        return week_start(today())
    try:
        return week_start(date.fromisoformat(str(value)))
    except ValueError:
        raise AppError('주는 YYYY-MM-DD 형식의 날짜로 골라 주세요.')


def parse_month(value):
    """보려는 달('YYYY-MM'). 없으면 이번 달. 달의 첫날을 돌려준다."""
    if not value:
        return today().replace(day=1)
    try:
        return date.fromisoformat(str(value) + '-01')
    except ValueError:
        raise AppError('달은 YYYY-MM 형식으로 골라 주세요.')


def shift_month(first, months):
    index = first.year * 12 + first.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def category(row):
    """합계에서 나누는 갈래: 재획(hunt), 주간 보스(boss), 월간 보스(monthly — 검은 마법사)."""
    if row['kind'] == 'boss' and str(row.get('boss') or '').startswith(MONTHLY_BOSSES):
        return 'monthly'
    return row['kind']


def breakdown(rows):
    """기록 묶음의 합계: 재획·주보·월보(검은 마법사)·전체, 그리고 캐릭터별(큰 순)."""
    out = {'hunt': 0.0, 'boss': 0.0, 'monthly': 0.0, 'hunt_count': 0, 'boss_count': 0, 'monthly_count': 0}
    people = {}
    for r in rows:
        kind = category(r)
        out[kind] += r['total']
        out[kind + '_count'] += 1
        who = people.setdefault(r.get('character') or NO_CHARACTER,
                                {'name': r.get('character') or NO_CHARACTER, 'hunt': 0.0, 'boss': 0.0, 'monthly': 0.0, 'total': 0.0})
        who[kind] += r['total']
        who['total'] += r['total']
    out['total'] = out['hunt'] + out['boss'] + out['monthly']
    out['characters'] = sorted(people.values(), key=lambda c: (c['name'] == NO_CHARACTER, -c['total']))
    return out


def hunt_days(rows):
    """날짜별 재획 합계: {날짜: {total, meso, pieces, flasks, count}}."""
    days = {}
    for r in rows:
        d = days.setdefault(r['day'], {'total': 0.0, 'meso': 0.0, 'pieces': 0, 'flasks': 0.0, 'count': 0})
        d['total'] += r['total']
        d['meso'] += r['meso'] or 0
        d['pieces'] += r['pieces'] or 0
        d['flasks'] += r['flasks'] or 0
        d['count'] += 1
    return days


CAPTURE_TYPES = ('image/png', 'image/jpeg', 'image/webp')


def read_capture(model, selected, image):
    """게임 캡처(data URL)에서 인벤토리·창고 메소와 조각 개수를 읽는다. 저장하지 않는다 — 사용자가 확인한 뒤 기록한다."""
    head, _, data = str(image or '').partition(',')
    mime = head[5:].split(';')[0] if head.startswith('data:') else ''
    if mime not in CAPTURE_TYPES or ';base64' not in head or not data:
        raise AppError('PNG·JPG 캡처 이미지를 붙여 넣어 주세요.')
    raw, meta = model.read_capture(selected, mime, data)
    values = {}
    for k, v in raw.items():
        if k == 'sol_erda_pieces':
            # '12+147'(인벤+창고)처럼 나눠 적을 수 있다. 숫자 묶음을 더한다.
            parts = [int(n) for n in re.findall(r'\d+', (v or '').replace(',', ''))]
            values[k] = sum(parts) if parts and sum(parts) <= 100000 else None
        elif v and re.fullmatch(r'\s*0+\s*(?:메소)?\s*', v):
            values[k] = 0.0          # '0'은 금액이 없는 게 아니라 0메소다(사냥 전 0메소에서 시작, 2026-10-02 사용자 보고)
            continue
        else:
            amount = parse_price(v) if v else None
            values[k] = amount if amount is not None and amount <= MAX_MESO else None
    return {'raw': raw, 'values': values, 'model': meta.get('model') if isinstance(meta, dict) else None}


def overview(store, week=None, month=None, limit=200):
    """수익 화면 전체. 고른 주(목요일 시작)·고른 달(달력 기준)의 합계와 캐릭터별, 최근 12주·6개월 흐름, 그 주의 기록 목록.

    주간은 게임의 주간 초기화(목요일 0시)에 맞춘다. 지난주·지난달도 week·month로 골라 본다.
    """
    ensure(store)
    rows = store.rows('SELECT * FROM earnings ORDER BY day DESC, created_at DESC')
    for row in rows:
        row['total'] = total(row)
    week_from = parse_week(week)
    week_to = week_from + timedelta(days=6)
    month_from = parse_month(month)
    month_to = shift_month(month_from, 1) - timedelta(days=1)
    # 흐름 막대는 이번 주·이번 달에서 끝나게 고정한다. 지난 막대를 눌러도 막대가 밀리지 않고 고른 막대만 바뀌어,
    # 다시 앞(최근) 막대를 누를 수 있다(2026-10-01 사용자 보고). 12주·6개월보다 더 과거를 고르면 그 주·달이 끝에 오게 민다.
    week_anchor = week_start(today())
    if week_from < week_anchor - timedelta(weeks=11) or week_from > week_anchor:
        week_anchor = week_from
    month_anchor = today().replace(day=1)
    if month_from < shift_month(month_anchor, -5) or month_from > month_anchor:
        month_anchor = month_from
    # 기록을 한 번만 순회해 12주·6개월로 묶는다. 날짜 문자열 변환도 기록마다 반복하지 않는다.
    week_keys = [(week_anchor - timedelta(weeks=back)).isoformat() for back in range(11, -1, -1)]
    month_keys = [shift_month(month_anchor, -back).isoformat()[:7] for back in range(5, -1, -1)]
    week_rows = {key: [] for key in week_keys}
    month_rows = {key: [] for key in month_keys}
    week_end = (week_anchor + timedelta(days=6)).isoformat()
    for r in rows:
        if week_keys[0] <= r['day'] <= week_end:
            week_rows[week_start(date.fromisoformat(r['day'])).isoformat()].append(r)
        month_key = r['day'][:7]
        if month_key in month_rows:
            month_rows[month_key].append(r)
    in_week = week_rows[week_from.isoformat()]
    in_month = month_rows[month_from.isoformat()[:7]]
    weeks = [{'week_start': key, **{k: v for k, v in breakdown(week_rows[key]).items() if k != 'characters'}}
             for key in week_keys]
    months = [{'month': key, **{k: v for k, v in breakdown(month_rows[key]).items() if k != 'characters'}}
              for key in month_keys]

    hunts_all = [r for r in rows if r['kind'] == 'hunt']
    flasks = sum(r['flasks'] or 0 for r in hunts_all)
    everything = breakdown(rows)
    choices, default_character = character_choices(store, rows)
    return {'week': {'start': week_from.isoformat(), 'end': week_to.isoformat(), 'current': week_from == week_start(today()),
                     **breakdown(in_week)},
            'month': {'month': month_from.isoformat()[:7], 'start': month_from.isoformat(), 'end': month_to.isoformat(),
                      'current': month_from == today().replace(day=1), **breakdown(in_month)},
            'all': {**everything, 'pieces': sum(r['pieces'] or 0 for r in hunts_all),
                    'hunt_average': (sum(r['total'] for r in hunts_all) / len(hunts_all)) if hunts_all else None,
                    'per_flask': (sum(r['total'] for r in hunts_all if r['flasks']) / flasks) if flasks else None,
                    'first_day': rows[-1]['day'] if rows else None},
            'weeks': weeks, 'months': months,
            'hunts': [r for r in in_week if r['kind'] == 'hunt'][:limit],
            # 재획 달력: 고른 달의 재획 기록과 날짜별 합계(화면이 목요일 시작 주로 칸을 나눈다)
            'month_hunts': [r for r in in_month if r['kind'] == 'hunt'][:limit * 3],
            'hunt_days': hunt_days(r for r in in_month if r['kind'] == 'hunt'),
            'bosses': [r for r in in_week if r['kind'] == 'boss'][:limit],
            'characters': [c['name'] for c in choices], 'character_choices': choices,
            'default_character': default_character, 'account_loaded': bool(store.setting(ACCOUNT_CHARACTERS)),
            'piece_price': store.setting(PIECE_PRICE) or None, 'piece_auction': store.setting(PIECE_AUCTION) or None,
            'boss_prices': store.setting(BOSS_PRICES) or {},
            'boss_limit': WEEKLY_BOSS_LIMIT,
            'boss_counts': {name: sum(1 for r in in_week if r['kind'] == 'boss' and (r.get('character') or '') == name
                                      and counts_toward_limit(r['boss']))
                            for name in {r.get('character') or '' for r in in_week if r['kind'] == 'boss'}},
            'monthly_done': [c['name'] for c in choices if monthly_boss_done(store, c['name'], today())]
                            + ([''] if monthly_boss_done(store, None, today()) else []),
            'reboot_characters': [name for name, world in worlds(store).items() if world in REBOOT_WORLDS],
            'reboot_rate': REBOOT_RATE,
            'crystals': [{'label': crystal_label(b, d), 'name': b, 'difficulty': d, 'price': crystal_price(b, d)}
                         for b, d, _, _ in CRYSTALS],
            'crystal_source': CRYSTAL_SOURCE, 'crystal_community_source': COMMUNITY_SOURCE,
            'crystal_alert': store.setting('crystal_price_alert') or None}


# 스케줄러(넥슨 API)는 난이도를 영어로 준다(easy·normal·hard·chaos·extreme). 결정석 표는 한국어라 맞춰 읽는다(2026-10-03 사용자 보고:
# 주보 불러오기에서 결정석 가격이 안 채워짐 — 실제 응답 확인 '자쿰' 'chaos').
DIFFICULTY_KO = {'easy': '이지', 'normal': '노멀', 'hard': '하드', 'chaos': '카오스', 'extreme': '익스트림'}


def difficulty_ko(value):
    text = str(value or '').strip()
    return DIFFICULTY_KO.get(text.lower(), text)


def crystal_row(name, difficulty):
    """결정석 표의 한 줄. 이름이 똑같은 줄을 먼저('힐라'가 '진 힐라'로 잡히지 않게), 없으면 짧은 이름('세렌' → '선택받은 세렌')."""
    key, level = _squash(name), _squash(difficulty_ko(difficulty))
    if not key or not level:
        return None
    rows = [r for r in CRYSTALS if r[1] == level]
    return (next((r for r in rows if _squash(r[0]) == key), None)
            or next((r for r in rows if key in _squash(r[0]) or _squash(r[0]) in key), None))


def table_name(name, difficulty):
    """스케줄러의 짧은 이름('세렌')을 결정석 표 이름('선택받은 세렌')으로. 표에 없으면 그대로."""
    row = crystal_row(name, difficulty)
    return row[0] if row else name


def boss_label(boss):
    level = difficulty_ko(boss.get('difficulty'))
    return crystal_label(table_name(boss['name'], level), level)


def scheduled_bosses(store, nexon, names):
    """관리 중인 캐릭터들의 스케줄러에서 이번 주에 잡은 보스를 모은다.

    이미 기록한 보스(같은 주·캐릭터·보스)는 표시만 하고 다시 저장하지 않게 한다.
    결정석 가격과 파티 인원은 API가 주지 않아, 기억해 둔 가격만 미리 채운다.
    """
    ensure(store)
    week = week_start(today()).isoformat()
    recorded = {r['source_key'] for r in store.rows('SELECT source_key FROM earnings WHERE source_key IS NOT NULL')}
    remembered = store.setting(BOSS_PRICES) or {}
    characters, bosses = [], []
    for name in names:
        try:
            state = nexon.scheduler(name)
        except AppError as e:
            characters.append({'name': name, 'error': str(e)})
            continue
        characters.append({'name': state['character'], 'level': state.get('level'), 'job': state.get('job'),
                           'weekly_clear': state.get('weekly_clear'), 'weekly_limit': state.get('weekly_limit'),
                           'world': state.get('world'), 'reboot': is_reboot(store, state['character'], state.get('world'))})
        for boss in state['bosses']:
            if not boss['complete']:
                continue
            label = boss_label(boss)
            key = f"{week}|{state['character']}|{label}"
            # 이전 버전은 스케줄러 이름·영어 난이도 그대로 키를 만들었다('세렌 (hard)'). 그 키로 이미 기록했으면 기록한 것으로 본다.
            legacy = f"{week}|{state['character']}|{crystal_label(boss['name'], boss.get('difficulty'))}"
            official = crystal_for(store, state['character'], boss['name'], boss.get('difficulty'), world=state.get('world'))
            # 월간 보스는 스케줄러가 한 달 내내 완료로 보여 줘서, 주 키 대신 이번 달 기록 여부로 본다.
            monthly = label.startswith(MONTHLY_BOSSES) and monthly_boss_done(store, state['character'], today())
            bosses.append({'key': key, 'character': state['character'], 'boss': label, 'cycle': boss.get('cycle'),
                           'recorded': key in recorded or legacy in recorded or monthly, 'price': official or remembered.get(label),
                           'price_source': ('community' if crystal_from_community(boss['name'], boss.get('difficulty')) else 'official') if official
                           else ('remembered' if remembered.get(label) else None)})
    for c in characters:
        if not c.get('error'):
            c['recorded_count'] = weekly_boss_count(store, c['name'], week_start(today()))
    return {'week_start': week, 'characters': characters, 'bosses': bosses, 'boss_limit': WEEKLY_BOSS_LIMIT}

# 강렬한 힘의 결정 판매 가격. 공식 공지(메이플스토리 업데이트 813, '보스 리워드 개편')의 표를 옮겼다.
# 사용자가 공지 내용을 붙여 준 것으로, 이 컨테이너에서 공지 페이지를 직접 열어 대조하지는 못했다(2026-09-28).
# (보스, 난이도, 기존 가격, 변경 가격). 검은 마법사만 2026-10-01(목)부터 변경 가격이 적용된다.
CRYSTAL_SOURCE = {'name': '메이플스토리 업데이트 813 보스 리워드 개편', 'official': True,
                  'url': 'https://maplestory.nexon.com/news/update/813', 'recorded': '2026-09-28'}
CRYSTALS = (
    ('자쿰', '카오스', 8_080_000, 4_040_000),
    ('피에르', '카오스', 8_170_000, 4_080_000),
    ('반반', '카오스', 8_150_000, 4_070_000),
    ('블러디퀸', '카오스', 8_140_000, 4_070_000),
    ('벨룸', '카오스', 9_280_000, 4_640_000),
    ('매그너스', '하드', 8_560_000, 4_280_000),
    ('파풀라투스', '카오스', 13_100_000, 6_550_000),
    ('스우', '노멀', 16_700_000, 8_350_000),
    ('데미안', '노멀', 17_500_000, 8_750_000),
    ('가디언 엔젤 슬라임', '노멀', 25_500_000, 12_700_000),
    ('루시드', '이지', 29_800_000, 14_900_000),
    ('윌', '이지', 32_300_000, 16_100_000),
    ('루시드', '노멀', 35_600_000, 17_800_000),
    ('윌', '노멀', 41_100_000, 20_500_000),
    ('더스크', '노멀', 44_000_000, 22_000_000),
    ('듄켈', '노멀', 47_500_000, 23_700_000),
    ('데미안', '하드', 48_900_000, 46_400_000),
    ('스우', '하드', 51_500_000, 48_900_000),
    ('진 힐라', '노멀', 71_200_000, 67_600_000),
    ('루시드', '하드', 62_900_000, 59_700_000),
    ('더스크', '카오스', 69_800_000, 66_300_000),
    ('가디언 엔젤 슬라임', '카오스', 75_100_000, 71_300_000),
    ('윌', '하드', 77_100_000, 73_200_000),
    ('듄켈', '하드', 94_400_000, 89_600_000),
    ('진 힐라', '하드', 106_000_000, 100_000_000),
    ('선택받은 세렌', '노멀', 239_000_000, 167_000_000),
    ('감시자 칼로스', '이지', 280_000_000, 238_000_000),
    ('최초의 대적자', '이지', 308_000_000, 261_000_000),
    ('선택받은 세렌', '하드', 356_000_000, 302_000_000),
    ('카링', '이지', 377_000_000, 320_000_000),
    ('벨로나', '이지', 440_000_000, 396_000_000),
    ('감시자 칼로스', '노멀', 505_000_000, 479_000_000),
    ('최초의 대적자', '노멀', 560_000_000, 532_000_000),
    ('스우', '익스트림', 574_000_000, 545_000_000),
    ('찬란한 흉성', '노멀', 625_000_000, 576_000_000),
    ('카링', '노멀', 678_000_000, 593_000_000),
    ('벨로나', '노멀', 850_000_000, 824_000_000),
    ('림보', '노멀', 1_026_000_000, 995_000_000),
    ('감시자 칼로스', '카오스', 1_273_000_000, 1_230_000_000),
    ('발드릭스', '노멀', 1_368_000_000, 1_320_000_000),
    ('최초의 대적자', '하드', 1_435_000_000, 1_390_000_000),
    ('유피테르', '노멀', 1_615_000_000, 1_560_000_000),
    ('카링', '하드', 1_739_000_000, 1_560_000_000),
    ('선택받은 세렌', '익스트림', 2_835_000_000, 1_840_000_000),
    ('검은 마법사', '하드', 665_000_000, 465_000_000),
    ('검은 마법사', '익스트림', 8_740_000_000, 5_680_000_000),
)
# 공지 표에 없던 보스·난이도. 커뮤니티 정리표(정심심 블로그 '메이플스토리 보스 결정 가격 정리', 2026-09-17 수정, 본섭 패치 반영)에서 옮겼다(2026-10-03 사용자가 링크).
# 그 표의 공지 46개 값은 위 표와 모두 같았다. 표 머리 'HARD'는 보스에 따라 하드·카오스(편의상 통일) — 게임 난이도 이름에 맞췄다. 변경 전 가격이 따로 없어 같은 값.
COMMUNITY_SOURCE = {'name': '정심심 블로그 — 메이플스토리 보스 결정 가격 정리(2026-09-17 수정)', 'official': False,
                    'url': 'https://matsu1207.tistory.com/757', 'recorded': '2026-10-03'}
COMMUNITY_CRYSTALS = tuple((boss, diff, price, price) for boss, diff, price in (
    ('자쿰', '이지', 114_000), ('자쿰', '노멀', 349_000),
    ('매그너스', '이지', 411_000), ('매그너스', '노멀', 1_160_000),
    ('힐라', '노멀', 455_000), ('힐라', '하드', 1_280_000),
    ('카웅', '노멀', 712_000),
    ('파풀라투스', '이지', 390_000), ('파풀라투스', '노멀', 1_200_000),
    ('피에르', '노멀', 551_000), ('반반', '노멀', 551_000), ('블러디퀸', '노멀', 551_000), ('벨룸', '노멀', 551_000),
    ('반 레온', '이지', 602_000), ('반 레온', '노멀', 830_000), ('반 레온', '하드', 1_070_000),
    ('혼테일', '이지', 502_000), ('혼테일', '노멀', 576_000), ('혼테일', '카오스', 770_000),
    ('아카이럼', '이지', 656_000), ('아카이럼', '노멀', 1_110_000),
    ('핑크빈', '노멀', 799_000), ('핑크빈', '카오스', 1_320_000),
    ('시그너스', '노멀', 1_360_000),
    ('감시자 칼로스', '익스트림', 4_104_000_000),
    ('카링', '익스트림', 5_387_000_000),
    ('림보', '하드', 2_385_000_000),
    ('발드릭스', '하드', 3_078_000_000),
    ('최초의 대적자', '익스트림', 4_712_000_000),
    ('찬란한 흉성', '하드', 2_678_000_000),
    ('유피테르', '하드', 4_845_000_000),
    ('벨로나', '하드', 2_950_000_000),
))
COMMUNITY_KEYS = {(boss, diff) for boss, diff, _, _ in COMMUNITY_CRYSTALS}
CRYSTALS = CRYSTALS + COMMUNITY_CRYSTALS
DELAYED = {'검은 마법사': '2026-10-01'}   # 이 날짜 전 기록에는 기존 가격을 쓴다.


def _squash(text):
    return re.sub(r'\s+', '', str(text or ''))


# 리부트 월드(에오스·헬리오스)는 결정석 시세가 절반이다(2026-10-03 사용자: "에오스·헬리오스 캐릭터는 메소 계산을 반토막, 특히 보스").
# 앱이 자동으로 채우는 공식 결정석 가격에만 적용한다. 사용자가 직접 적은 금액(재획 메소·직접 적은 결정석 값)은 그대로.
REBOOT_WORLDS = ('에오스', '헬리오스')
REBOOT_RATE = 0.5


def worlds(store):
    """캐릭터 이름 → 월드. 관리 중인 캐릭터의 최근 조회 기록, 계정 캐릭터 목록(이쪽이 우선) 순으로 채운다."""
    out = {}
    for c in store.characters():
        world = next(((s.get('data') or {}).get('world') for s in c.get('snapshots') or [] if (s.get('data') or {}).get('world')), None)
        if world:
            out[c['name']] = world
    for c in store.setting(ACCOUNT_CHARACTERS) or []:
        if c.get('name') and c.get('world'):
            out[c['name']] = c['world']
    return out


def world_of(store, character):
    return worlds(store).get(character) if character else None


def is_reboot(store, character, world=None):
    return (world or world_of(store, character) or '') in REBOOT_WORLDS


def crystal_for(store, character, name, difficulty, day=None, world=None):
    """그 캐릭터 기준 결정석 판매가(리부트면 절반)."""
    price = crystal_price(name, difficulty, day)
    if price and is_reboot(store, character, world):
        return round(price * REBOOT_RATE)
    return price


def crystal_price(name, difficulty, day=None):
    """보스 이름·난이도의 결정석 판매가. 스케줄러의 짧은 이름('세렌')도 표의 이름('선택받은 세렌')과 맞춘다."""
    row = crystal_row(name, difficulty)
    if not row:
        return None
    boss, _, old, new = row
    since = DELAYED.get(boss)
    when = str(day or today().isoformat())
    return old if since and when < since else new


def crystal_from_community(name, difficulty):
    row = crystal_row(name, difficulty)
    return bool(row) and (row[0], row[1]) in COMMUNITY_KEYS


def crystal_label(name, difficulty):
    return f"{name} ({difficulty})" if difficulty else name


def parse_label(label):
    """'선택받은 세렌 (하드)' -> ('선택받은 세렌', '하드')."""
    found = re.fullmatch(r'\s*(.+?)\s*\((.+?)\)\s*', str(label or ''))
    return (found[1], found[2]) if found else (str(label or '').strip(), None)
