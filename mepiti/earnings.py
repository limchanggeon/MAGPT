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
MAX_MESO = 10_000_0000_0000      # 1경. 오타로 자릿수가 크게 넘어가는 것만 막는다.
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
    with store.db() as db:
        db.executescript(SCHEMA)
        # 스케줄러로 불러온 기록의 캐릭터와 중복 방지 키. 예전 표에는 없어 추가만 한다.
        columns = [r['name'] for r in db.execute('PRAGMA table_info(earnings)')]
        for column in ('character', 'source_key'):
            if column not in columns:
                db.execute(f'ALTER TABLE earnings ADD COLUMN {column} TEXT')


def today():
    return datetime.now(KST).date()


def week_start(day):
    """그 날짜가 속한 주의 목요일(주간 보스 초기화일)."""
    return day - timedelta(days=(day.weekday() - 3) % 7)


def meso(value, label, required=False):
    """'12억', '3,500만', '2천만', 숫자를 메소로. 빈칸이면 0(필수면 오류)."""
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
        amount = parse_price(str(value))
        if amount is None:
            raise AppError(f"{label}을(를) 읽지 못했습니다. '12억', '3500만'처럼 적어 주세요.")
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
        row['piece_price'] = meso(data.get('piece_price'), '조각 가격')
        flasks = count(data.get('flasks'), '재획비 개수', 0, 100, 0)
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
            row['crystal'] = crystal_price(*parse_label(boss), day.isoformat()) or 0.0
        row['party'] = int(count(data.get('party'), '파티 인원', 1, 6, 1))
        row['extra'] = meso(data.get('extra'), '추가 드롭 수익')
        if not row['crystal'] and not row['extra']:
            raise AppError('결정석 판매가나 추가 드롭 수익 중 하나는 적어 주세요.')
        if row['source_key'] and store.rows('SELECT 1 FROM earnings WHERE source_key=?', (row['source_key'],)):
            raise AppError(f"{row['character'] or ''} {boss}은(는) 이번 주에 이미 기록했습니다.".strip(), 409)
        if row['crystal'] and row['crystal'] != crystal_price(*parse_label(boss), day.isoformat()):
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
    managed = sorted(store.characters(), key=lambda c: not c.get('main'))
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


def breakdown(rows):
    """기록 묶음의 합계: 재획·주보·전체, 그리고 캐릭터별(큰 순)."""
    out = {'hunt': 0.0, 'boss': 0.0, 'hunt_count': 0, 'boss_count': 0}
    people = {}
    for r in rows:
        out[r['kind']] += r['total']
        out[r['kind'] + '_count'] += 1
        who = people.setdefault(r.get('character') or NO_CHARACTER,
                                {'name': r.get('character') or NO_CHARACTER, 'hunt': 0.0, 'boss': 0.0, 'total': 0.0})
        who[r['kind']] += r['total']
        who['total'] += r['total']
    out['total'] = out['hunt'] + out['boss']
    out['characters'] = sorted(people.values(), key=lambda c: (c['name'] == NO_CHARACTER, -c['total']))
    return out


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
    in_week = [r for r in rows if week_from.isoformat() <= r['day'] <= week_to.isoformat()]
    in_month = [r for r in rows if month_from.isoformat() <= r['day'] <= month_to.isoformat()]

    # 흐름: 고른 주까지 12주, 고른 달까지 6개월.
    weeks = []
    for back in range(11, -1, -1):
        start = week_from - timedelta(weeks=back)
        picked = [r for r in rows if start.isoformat() <= r['day'] <= (start + timedelta(days=6)).isoformat()]
        weeks.append({'week_start': start.isoformat(), **{k: v for k, v in breakdown(picked).items() if k != 'characters'}})
    months = []
    for back in range(5, -1, -1):
        start = shift_month(month_from, -back)
        stop = shift_month(start, 1) - timedelta(days=1)
        picked = [r for r in rows if start.isoformat() <= r['day'] <= stop.isoformat()]
        months.append({'month': start.isoformat()[:7], **{k: v for k, v in breakdown(picked).items() if k != 'characters'}})

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
            'bosses': [r for r in in_week if r['kind'] == 'boss'][:limit],
            'characters': [c['name'] for c in choices], 'character_choices': choices,
            'default_character': default_character, 'account_loaded': bool(store.setting(ACCOUNT_CHARACTERS)),
            'piece_price': store.setting(PIECE_PRICE) or None, 'piece_auction': store.setting(PIECE_AUCTION) or None,
            'boss_prices': store.setting(BOSS_PRICES) or {},
            'crystals': [{'label': crystal_label(b, d), 'name': b, 'difficulty': d, 'price': crystal_price(b, d)}
                         for b, d, _, _ in CRYSTALS],
            'crystal_source': CRYSTAL_SOURCE,
            'crystal_alert': store.setting('crystal_price_alert') or None}


def boss_label(boss):
    return crystal_label(boss['name'], boss.get('difficulty'))


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
                           'weekly_clear': state.get('weekly_clear'), 'weekly_limit': state.get('weekly_limit')})
        for boss in state['bosses']:
            if not boss['complete']:
                continue
            label = boss_label(boss)
            key = f"{week}|{state['character']}|{label}"
            official = crystal_price(boss['name'], boss.get('difficulty'))
            bosses.append({'key': key, 'character': state['character'], 'boss': label, 'cycle': boss.get('cycle'),
                           'recorded': key in recorded, 'price': official or remembered.get(label),
                           'price_source': 'official' if official else ('remembered' if remembered.get(label) else None)})
    return {'week_start': week, 'characters': characters, 'bosses': bosses}

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
DELAYED = {'검은 마법사': '2026-10-01'}   # 이 날짜 전 기록에는 기존 가격을 쓴다.


def _squash(text):
    return re.sub(r'\s+', '', str(text or ''))


def crystal_price(name, difficulty, day=None):
    """보스 이름·난이도의 결정석 판매가. 스케줄러의 짧은 이름('세렌')도 표의 이름('선택받은 세렌')과 맞춘다."""
    key, level = _squash(name), _squash(difficulty)
    if not key or not level:
        return None
    for boss, diff, old, new in CRYSTALS:
        full = _squash(boss)
        if diff == level and (full == key or key in full or full in key):
            since = DELAYED.get(boss)
            when = str(day or today().isoformat())
            return old if since and when < since else new
    return None


def crystal_label(name, difficulty):
    return f"{name} ({difficulty})" if difficulty else name


def parse_label(label):
    """'선택받은 세렌 (하드)' -> ('선택받은 세렌', '하드')."""
    found = re.fullmatch(r'\s*(.+?)\s*\((.+?)\)\s*', str(label or ''))
    return (found[1], found[2]) if found else (str(label or '').strip(), None)
