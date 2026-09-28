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
           'extra': None, 'note': note, 'created_at': now()}
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
        row['party'] = int(count(data.get('party'), '파티 인원', 1, 6, 1))
        row['extra'] = meso(data.get('extra'), '추가 드롭 수익')
        if not row['crystal'] and not row['extra']:
            raise AppError('결정석 판매가나 추가 드롭 수익 중 하나는 적어 주세요.')
    else:
        raise AppError('수익 종류는 재획 또는 주보만 기록할 수 있습니다.')
    with store.db() as db:
        db.execute('INSERT INTO earnings VALUES(:id,:kind,:day,:meso,:pieces,:piece_price,:flasks,'
                   ':boss,:crystal,:party,:extra,:note,:created_at)', row)
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
            'summary': summary, 'piece_price': store.setting(PIECE_PRICE) or None}
