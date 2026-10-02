"""스타포스 강화 기록 — 실제로 쓴 돈과 기대값 비교.

넥슨 history/starforce는 계정 단위로 하루치 강화 결과를 준다(2023-12-27 이후).
**쓴 메소는 응답에 없어서** 시도마다 그 성의 강화 비용을 이 앱의 비용식(mesulive 이식)으로 다시 계산한다.
  실제 비용 = 시도별 강화 비용(저장한 강화 조건의 이벤트·MVP 할인, 기록의 파괴방지 반영) + 파괴 횟수 x 노작값
  기대값    = 같은 시작 성 -> 도달한 최고 성, 같은 조건으로 계산한 평균 비용
장비 레벨도 응답에 없어, 캐릭터의 현재 장비(프리셋 포함)에서 같은 이름을 찾는다. 못 찾으면 사용자가 적는다.
"""
import json
from collections import Counter
from datetime import date, timedelta

from . import conditions, prices, starforce
from .core import AppError
from .earnings import today

FIRST_DAY = date(2023, 12, 27)   # 넥슨이 이 날부터의 기록만 준다.
FETCHED = 'sf_history_days'      # 이미 받아 둔 날짜. 지난 날은 다시 받지 않는다.
LEVELS = 'sf_item_levels'        # 장비 이름 -> 장비 레벨

SCHEMA = '''
CREATE TABLE IF NOT EXISTS starforce_history(
  id TEXT PRIMARY KEY, character TEXT, world TEXT, item TEXT NOT NULL,
  before INTEGER NOT NULL, after INTEGER NOT NULL, result TEXT, starcatch TEXT,
  safeguard INTEGER, created TEXT NOT NULL, events TEXT, superior INTEGER);
CREATE INDEX IF NOT EXISTS starforce_history_item ON starforce_history(character, item);
'''


# 이름만으로 장비 레벨이 확실한 세트. 착용하지 않은 장비도 계산할 수 있게 한다.
SUPERIOR_FIXED = 'starforce_superior_fixed'   # 잘못 저장된 슈페리얼 표시를 바로잡았는가
NAME_LEVELS = (('에테르넬', 250), ('아케인셰이드', 200), ('제네시스', 200), ('앱솔랩스', 160))


def guess_level(item):
    return next((level for word, level in NAME_LEVELS if word in (item or '')), None)


def ensure(store):
    with store.schema('starforce_history') as needed:
        if needed:
            with store.db() as db:
                db.executescript(SCHEMA)
                if 'superior' not in [r['name'] for r in db.execute('PRAGMA table_info(starforce_history)')]:
                    db.execute('ALTER TABLE starforce_history ADD COLUMN superior INTEGER')
        # 보정 표시가 초기화된 경우도 재시도할 수 있어야 한다.
        if not store.setting(SUPERIOR_FIXED):
            with store.db() as db:
                db.execute("UPDATE starforce_history SET superior=CASE WHEN item LIKE '%타일런트%' THEN 1 ELSE 0 END WHERE superior=1")
            store.set_setting(SUPERIOR_FIXED, '1')


def destroyed(row):
    return '파괴' in (row.get('result') or '')


def fetch(store, nexon, days=14):
    """최근 days일의 기록을 받는다. 오늘·어제는 늘 다시 받고, 그 전 날은 한 번 받은 뒤 건너뛴다."""
    ensure(store)
    days = int(days)
    if not 1 <= days <= 90:
        raise AppError('기록은 한 번에 1~90일까지 불러올 수 있습니다.')
    fetched = set(store.setting(FETCHED) or [])
    now, added, asked, failed = today(), 0, 0, []
    blocked = False
    for back in range(days):
        day = now - timedelta(days=back)
        if day < FIRST_DAY:
            break
        key = day.isoformat()
        if key in fetched and back > 1:
            continue
        asked += 1
        try:
            rows = nexon.starforce_history(key)
        except AppError as e:
            failed.append(f'{key}: {e}')
            if getattr(e, 'upstream', e.status) in (401, 403, 429):
                blocked = True
                break          # 키 문제나 한도 초과면 더 부르지 않는다.
            continue
        with store.db() as db:
            for r in rows:
                # 이미 받은 기록이면 슈페리얼 표시만 새로 받은 값으로 고친다(예전 판별 오류를 다시 받기로도 바로잡게).
                added += db.execute('INSERT INTO starforce_history(id,character,world,item,before,after,result,'
                                    'starcatch,safeguard,created,events,superior) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) '
                                    'ON CONFLICT(id) DO UPDATE SET superior=excluded.superior WHERE superior IS NOT excluded.superior',
                                    (r['id'], r['character'], r['world'], r['item'], r['before'], r['after'],
                                     r['result'], r['starcatch'], int(bool(r['safeguard'])), r['created'],
                                     json.dumps(r.get('events') or [], ensure_ascii=False),
                                     int(bool(r.get('superior'))))).rowcount
        fetched.add(key)
    store.set_setting(FETCHED, sorted(fetched)[-400:])
    if not blocked:
        resolve_levels(store, nexon)
    return {'days': days, 'requested_days': asked, 'added': added, 'failed': failed[:5]}


def resolve_levels(store, nexon):
    """레벨을 모르는 장비를 그 캐릭터의 장비(현재·프리셋)에서 찾아 기억한다."""
    levels = dict(store.setting(LEVELS) or {})
    pending = {}
    for r in store.rows('SELECT DISTINCT character, item FROM starforce_history'):
        if r['item'] not in levels and r['character']:
            pending.setdefault(r['character'], set()).add(r['item'])
    for character, items in list(pending.items())[:10]:
        try:
            profile = nexon.character(character, details=True)
        except AppError:
            continue
        rows = list(profile.get('equipment') or [])
        for preset in (profile.get('equipment_presets') or {}).values():
            rows += preset
        for item in rows:
            if item.get('name') in items and item.get('equip_level'):
                levels[item['name']] = item['equip_level']
    store.set_setting(LEVELS, levels)
    return levels


def set_level(store, item, level):
    item = str(item or '').strip()
    try:
        level = int(level)
    except (TypeError, ValueError):
        raise AppError('장비 레벨은 숫자로 적어 주세요.')
    if not item or not 1 <= level <= 300:
        raise AppError('장비 이름과 1~300 사이의 장비 레벨을 적어 주세요.')
    levels = dict(store.setting(LEVELS) or {})
    levels[item] = level
    store.set_setting(LEVELS, levels)
    return {'item': item, 'level': level}


def events_of(row):
    try:
        return json.loads(row.get('events') or '[]')
    except ValueError:
        return []


def active(events, star):
    """그 성에서 적용된 이벤트. 구간을 알 수 없으면 적용된 것으로 본다."""
    return [e for e in events if not e.get('range') or e['range'][0] <= star <= e['range'][1]]


def attempt_cost(level, star, safeguard, picked, events=()):
    """한 번 시도하는 데 든 메소. starforce.expected와 같은 규칙(MVP는 16성 이하, 이벤트 할인은 곱함).

    이벤트 할인은 **그 기록에 남은 강화 당시 이벤트**를 쓴다. MVP·PC방 할인은 기록에 없어 저장한 강화 조건을 쓴다.
    """
    base = starforce.attempt_costs(level)[star]
    here = active(events, star)
    discount = max((e.get('discount') or 0 for e in here), default=0)
    guaranteed = any((e.get('success_rate') or 0) >= 1 for e in here)
    mvp = sum(starforce.DISCOUNTS[d] for d in picked.get('discounts') or [] if d in starforce.DISCOUNTS)
    cost = round(base * (1 - mvp if star < starforce.DISCOUNT_MAX_STAR else 1) * (1 - discount))
    if safeguard and star in starforce.SAFEGUARD_STARS and not guaranteed:
        cost += base * 2
    return cost


def event_name(events, star):
    """기록의 이벤트를 앱의 이벤트 이름으로. 기대값 계산에 쓴다."""
    here = active(events, star)
    cut = any(e.get('discount') for e in here)
    safer = any(e.get('destroy_decrease') for e in here)
    restore = any(e.get('recovery_discount') for e in here)
    guaranteed = any((e.get('success_rate') or 0) >= 1 for e in events)
    if cut and safer:
        if guaranteed:
            return '샤타포스(15 16 포함)'
        return '샤타포스(+흔적 복구 비용 20% 할인)' if restore else '샤타포스'
    if cut:
        return '30% 할인'
    if safer:
        return '21성 이하 파괴 30% 감소'
    if restore:
        return '흔적 복구 비용 20% 할인'
    if guaranteed:
        return '5/10/15성 100%'
    if any(e.get('plus') for e in here):
        return '10성 이하 1+1'
    return '없음'


def analyse(store, character, item, rows, picked, levels):
    rows = sorted(rows, key=lambda r: r['created'])
    start, reached, end = rows[0]['before'], max(r['after'] for r in rows), rows[-1]['after']
    counts = {'attempts': len(rows), 'success': sum(r['after'] > r['before'] for r in rows),
              'destroy': sum(destroyed(r) for r in rows), 'safeguard': sum(bool(r['safeguard']) for r in rows)}
    counts['fail'] = counts['attempts'] - counts['success'] - counts['destroy']
    level = levels.get(item) or guess_level(item)
    spare = prices.resolve(store, item)
    group = {'character': character, 'item': item, 'level': level, 'start': start, 'reached': reached, 'end': end,
             'first': rows[0]['created'][:10], 'last': rows[-1]['created'][:10], **counts,
             'spare_price': spare['price'] if spare['known'] else None,
             'actual': None, 'expected': None, 'notes': [], 'missing': None,
             'level_guessed': bool(level and item not in levels)}
    if any(r.get('superior') for r in rows):
        group['missing'] = 'superior'
        group['notes'].append('슈페리얼 장비는 강화 확률·비용 규칙이 달라 계산하지 않았습니다.')
        return group
    if not level or level > 300 or max(r['before'] for r in rows) >= 30:
        group['missing'] = 'level'
        group['notes'].append('장비 레벨을 몰라 이득·손해를 계산하지 못했습니다. 아래에서 장비 레벨을 골라 주세요.')
        return group
    if group['level_guessed']:
        group['notes'].append(f'장비 레벨 {level}은 이름으로 추정했습니다.')
    costs = [attempt_cost(level, r['before'], r['safeguard'], picked, events_of(r)) for r in rows]
    spent = [cost + ((group['spare_price'] or 0) if destroyed(r) else 0) for cost, r in zip(costs, rows)]
    attempts_cost = sum(costs)
    # 기대값은 기록에 가장 많이 남은 이벤트로 계산한다. 파괴방지는 실제로 켠 구간만 켠다.
    event = Counter(event_name(events_of(r), r['before']) for r in rows).most_common(1)[0][0]
    guarded = sorted({r['before'] for r in rows if r['safeguard']} & set(starforce.SAFEGUARD_STARS))
    group['event'] = event
    picked = {**picked, 'event': event, 'safeguard': False}
    destroy_cost = counts['destroy'] * (group['spare_price'] or 0)
    # 기대값과는 '최고 성을 처음 찍을 때까지'만 비교한다. 그 뒤(더 높은 성 도전·파괴 후 복구)는 따로 보여 준다.
    first = next(i for i, r in enumerate(rows) if r['after'] == reached)
    until = rows[:first + 1]
    group['actual'] = {'attempts_cost': attempts_cost, 'destroy_cost': destroy_cost,
                       'total': attempts_cost + destroy_cost,
                       'to_reach': sum(spent[:first + 1]), 'to_reach_attempts': len(until),
                       'to_reach_destroys': sum(destroyed(r) for r in until),
                       'after': sum(spent[first + 1:]), 'after_attempts': len(rows) - len(until),
                       'after_destroys': counts['destroy'] - sum(destroyed(r) for r in until)}
    if group['spare_price'] is None and counts['destroy']:
        group['notes'].append('노작값을 몰라 파괴 비용을 0으로 두었습니다. 노작값을 적으면 다시 계산합니다.')
    if reached > start and reached <= starforce.reachable_star(level):
        try:
            calc = starforce.expected({'level': level, 'current_star': start, 'target_star': reached,
                                       'spare_cost': group['spare_price'] or 0,
                                       **conditions.to_arguments(picked, start, reached), 'safeguard': guarded})
            group['expected'] = {'cost': calc['expected_cost'], 'attempts': calc['expected_attempts'],
                                 'destroys': calc['expected_destroys']}
            group['difference'] = group['actual']['to_reach'] - calc['expected_cost']
            group['ratio'] = round(group['actual']['to_reach'] / calc['expected_cost'], 3) if calc['expected_cost'] else None
        except AppError as e:
            group['notes'].append(f'기대값을 계산하지 못했습니다: {e}')
    else:
        group['notes'].append('시작 성보다 높이 올라간 적이 없어 기대값과 비교하지 않았습니다.')
    return group


def overview(store):
    ensure(store)
    picked = conditions.load(store)
    levels = store.setting(LEVELS) or {}
    grouped = {}
    for r in store.rows('SELECT * FROM starforce_history ORDER BY created'):
        grouped.setdefault((r['character'] or '', r['item']), []).append(r)
    groups = [analyse(store, c, i, rows, picked, levels) for (c, i), rows in grouped.items()]
    groups.sort(key=lambda g: g['last'], reverse=True)
    fetched = store.setting(FETCHED) or []
    return {'groups': groups, 'conditions': conditions.summary(picked), 'fetched_days': len(fetched),
            'missing_level': sum(g['missing'] == 'level' for g in groups),
            'latest_day': fetched[-1] if fetched else None,
            'notes': ['쓴 메소는 넥슨 기록에 없어, 시도마다 그 성의 강화 비용을 비용식(mesulive 이식, 비공식)으로 다시 계산했습니다.',
                      '이벤트 할인은 기록에 남은 강화 당시 이벤트를 썼습니다. MVP·PC방 할인은 기록에 없어 저장한 강화 조건을 썼습니다.',
                      '기대값은 시작 성에서 도달한 최고 성까지, 기록에 가장 많이 남은 이벤트와 실제로 켠 파괴방지 구간으로 계산한 평균입니다.',
                      '기대값과는 최고 성을 처음 달성할 때까지 쓴 돈만 비교하고, 그 뒤의 도전·파괴 후 재강화 비용은 따로 보여 줍니다.',
                      '흔적 복구 비용은 기록으로 알 수 없어 넣지 않았습니다.']}
