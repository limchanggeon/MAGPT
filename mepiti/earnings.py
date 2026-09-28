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


def delete(store, record_id):
    ensure(store)
    with store.db() as db:
        if not db.execute('DELETE FROM earnings WHERE id=?', (str(record_id),)).rowcount:
            raise AppError('기록을 찾을 수 없습니다.', 404)
    return {'ok': True}


def overview(store, limit=200):
    """기록 목록과 합계. 주간은 목요일 시작, 월간은 달력 기준."""
    ensure(store)
    rows = store.rows('SELECT * FROM earnings ORDER BY day DESC, created_at DESC')
    for row in rows:
        row['total'] = total(row)
    day = today()
    this_week, this_month = week_start(day).isoformat(), day.replace(day=1).isoformat()

    def summed(kind, since=None):
        picked = [r for r in rows if r['kind'] == kind and (since is None or r['day'] >= since)]
        return {'count': len(picked), 'total': sum(r['total'] for r in picked)}

    hunts = [r for r in rows if r['kind'] == 'hunt']
    flasks = sum(r['flasks'] or 0 for r in hunts)
    summary = {
        'week_start': this_week,
        'hunt': {'week': summed('hunt', this_week), 'month': summed('hunt', this_month), 'all': summed('hunt'),
                 'pieces': sum(r['pieces'] or 0 for r in hunts),
                 'average': (sum(r['total'] for r in hunts) / len(hunts)) if hunts else None,
                 'per_flask': (sum(r['total'] for r in hunts if r['flasks']) / flasks) if flasks else None},
        'boss': {'week': summed('boss', this_week), 'month': summed('boss', this_month), 'all': summed('boss')},
    }
    summary['all'] = {period: summary['hunt'][period]['total'] + summary['boss'][period]['total']
                      for period in ('week', 'month', 'all')}
    # 주보는 주 단위로 묶어 보여 준다.
    weeks = {}
    for r in rows:
        if r['kind'] == 'boss':
            key = week_start(date.fromisoformat(r['day'])).isoformat()
            weeks.setdefault(key, {'week_start': key, 'count': 0, 'total': 0.0})
            weeks[key]['count'] += 1
            weeks[key]['total'] += r['total']
    return {'hunts': hunts[:limit], 'bosses': [r for r in rows if r['kind'] == 'boss'][:limit],
            'boss_weeks': sorted(weeks.values(), key=lambda w: w['week_start'], reverse=True)[:26],
            'summary': summary, 'piece_price': store.setting(PIECE_PRICE) or None,
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
