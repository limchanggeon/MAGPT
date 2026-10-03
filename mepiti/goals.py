"""목표 탭 — 메소 목표까지 걸릴 기간, 다음 레벨까지 걸릴 기간.

메소: 수익 기록으로 하루 평균 수익을 낸다.
  - 사냥(재획): 소재비 1개 = 30분. 소재비 1개당 수익(조각 포함) × 최근 7일 하루 평균 소재비 개수.
  - 주보: 최근 몇 주(최대 4주)의 주간 평균 ÷ 7.
  기록이 7일보다 짧으면 '기록이 쌓일수록 정확해진다'고 알린다. 앱이 지어낸 값은 없다(모두 사용자가 적은 기록).
경험치: 넥슨 Open API의 날짜별 캐릭터 기본 정보(레벨·경험치·경험치 %)로 최근 7일 하루 평균 경험치를 낸다.
  그 레벨의 필요 경험치 = 경험치 ÷ (경험치 % / 100). 지난날 값은 한 번 받아 저장해 다시 부르지 않는다.
"""
import math
from datetime import date, datetime, timedelta

from . import earnings
from .core import AppError, KST, now

FLASK_MINUTES = 30
WINDOW_DAYS = 7
MESO_GOAL = 'goal_meso'          # {'target': 메소, 'current': 메소}
EXP_DAYS = 'exp_days'            # {캐릭터: {'YYYY-MM-DD': {'level', 'exp', 'rate'}}}
BOSS_WEEKS = 4


def meso_plan(store, target=None, current=None):
    """메소 목표 계획. target·current를 주면 저장하고, 없으면 저장된 값을 쓴다."""
    earnings.ensure(store)
    saved = store.setting(MESO_GOAL) or {}
    if target is not None:
        saved = {'target': earnings.meso(target, '목표 메소', required=True),
                 'current': earnings.meso(current, '지금 가진 메소')}
        store.set_setting(MESO_GOAL, saved)
    day = earnings.today()
    # 평균에 필요한 최근 30일만 읽는다. 전체 이력은 시작 날짜·존재 여부만 확인한다.
    summary = store.rows('''SELECT COUNT(*) AS count, MIN(day) AS first,
        MIN(CASE WHEN kind='boss' THEN day END) AS first_boss,
        MAX(CASE WHEN kind='hunt' THEN 1 ELSE 0 END) AS has_hunts
        FROM earnings WHERE day<=?''', (day.isoformat(),))[0]
    month_ago = (day - timedelta(days=29)).isoformat()
    rows = store.rows('''SELECT kind, day, meso, pieces, piece_price, flasks, crystal, party, extra
        FROM earnings WHERE day>=? AND day<=? ORDER BY rowid''', (month_ago, day.isoformat()))
    for r in rows:
        r['total'] = earnings.total(r)
    first = date.fromisoformat(summary['first']) if summary['first'] else None
    # 기록한 첫날부터 오늘까지(최대 7일). 기록 없는 날도 '안 한 날'로 평균에 넣는다.
    data_days = min(WINDOW_DAYS, (day - first).days + 1) if first else 0
    since = (day - timedelta(days=data_days - 1)).isoformat() if data_days else None
    hunts = [r for r in rows if r['kind'] == 'hunt']
    recent = [r for r in hunts if since and r['day'] >= since]
    # 소재비 1개당 수익은 표본을 넉넉히(최근 30일, 소재비 개수를 적은 기록만).
    timed = [r for r in hunts if r['day'] >= month_ago and r['flasks']]
    flasks_timed = sum(r['flasks'] for r in timed)
    per_flask = sum(r['total'] for r in timed) / flasks_timed if flasks_timed else None
    flasks_recent = sum(r['flasks'] or 0 for r in recent)
    flasks_per_day = flasks_recent / data_days if data_days else 0.0
    untimed = [r for r in recent if not r['flasks']]
    if per_flask is not None:
        # 소재비를 적지 않은 기록도 수익에는 들어간다(시간만 모를 뿐).
        hunt_per_day = (per_flask * flasks_per_day) + (sum(r['total'] for r in untimed) / data_days if data_days else 0)
    else:
        hunt_per_day = sum(r['total'] for r in recent) / data_days if data_days else 0.0
    # 주보: 첫 주보 기록 주부터 이번 주까지(최대 4주)의 주간 평균.
    bosses = [r for r in rows if r['kind'] == 'boss']
    this_week = earnings.week_start(day)
    first_boss = earnings.week_start(date.fromisoformat(summary['first_boss'])) if summary['first_boss'] else None
    boss_weeks = min(BOSS_WEEKS, (this_week - first_boss).days // 7 + 1) if first_boss else 0
    boss_since = (this_week - timedelta(weeks=boss_weeks - 1)).isoformat() if boss_weeks else None
    boss_total = sum(r['total'] for r in bosses if boss_since and r['day'] >= boss_since)
    boss_per_week = boss_total / boss_weeks if boss_weeks else 0.0
    daily = hunt_per_day + boss_per_week / 7
    target_meso, current_meso = saved.get('target'), saved.get('current') or 0.0
    remaining = max(0.0, (target_meso or 0) - current_meso) if target_meso else None
    days_needed = math.ceil(remaining / daily) if remaining and daily > 0 else (0 if remaining == 0 else None)
    notes = []
    if not summary['count']:
        notes.append('수익 기록이 없어 계산할 수 없어요. 수익 탭에 재획·주보를 기록해 주세요.')
    elif data_days < WINDOW_DAYS:
        notes.append(f'기록이 {data_days}일치예요. 사냥 시간 평균은 7일쯤 쌓이면 정확해져요.')
    if summary['has_hunts'] and per_flask is None:
        notes.append('재획 기록에 소재비 개수를 적으면 사냥 시간(소재비 1개 = 30분)과 1개당 수익을 계산해요.')
    eta = expected_date(day, days_needed)
    if days_needed is not None and eta is None:
        notes.append('예상 기간이 너무 길어 도달 날짜를 표시할 수 없어요.')
    return {'target': target_meso, 'current': current_meso, 'remaining': remaining,
            'days': days_needed, 'eta': eta,
            'daily': daily, 'hunt_per_day': hunt_per_day, 'boss_per_week': boss_per_week, 'boss_weeks': boss_weeks,
            'per_flask': per_flask, 'flasks_per_day': flasks_per_day,
            'hours_per_day': flasks_per_day * FLASK_MINUTES / 60, 'data_days': data_days,
            'flask_minutes': FLASK_MINUTES, 'notes': notes}


def expected_date(day, days):
    if days is None or not math.isfinite(days) or days > (date.max - day).days or days < 0:
        return None
    return (day + timedelta(days=math.ceil(days))).isoformat()


def required(point):
    """그 레벨의 필요 경험치(경험치 ÷ 경험치 %)."""
    return point['exp'] / (point['rate'] / 100) if point['rate'] and point['rate'] > 0 else None


def gained(a, b):
    """두 시점 사이에 얻은 경험치. 레벨이 올랐으면 그 레벨의 남은 경험치 + 새 레벨에서 얻은 만큼(2레벨 이상 오른 날은 그 사이 레벨을 모른다)."""
    if b['level'] == a['level']:
        return max(0, b['exp'] - a['exp'])
    need = required(a)
    return max(0, (need - a['exp'] if need else 0) + b['exp'])


def exp_plan(store, nexon, name):
    """최근 7일 경험치 흐름과 다음 레벨까지 걸릴 날짜. 지난날 값은 저장해 두고 다시 부르지 않는다."""
    if not name:
        raise AppError('캐릭터를 골라 주세요.')
    with store.operation(('exp_days', name)):
        return _exp_plan(store, nexon, name)


def _exp_plan(store, nexon, name):
    cache = store.setting(EXP_DAYS) or {}
    days = dict(cache.get(name) or {})
    today = earnings.today()
    blocked = None
    for back in range(WINDOW_DAYS, 0, -1):
        key = (today - timedelta(days=back)).isoformat()
        if key not in days:
            try:
                days[key] = nexon.basic_on(name, key)
            except AppError as e:
                if getattr(e, 'upstream', e.status) in (401, 403, 429):
                    blocked = e
                    break
                continue                                    # 그날 기록이 없으면(캐릭터 생성 전 등) 건너뛴다
    keep = sorted(days)[-30:]
    with store.operation('exp_days_write'):
        cache = store.setting(EXP_DAYS) or {}
        cache[name] = {k: days[k] for k in keep}
        store.set_setting(EXP_DAYS, cache)
    if blocked:
        raise blocked
    latest = nexon.basic_on(name, None)                   # 지금 값(오늘 0시 이후 사냥 포함)
    points = [{'at': datetime.fromisoformat(k).replace(tzinfo=KST), **days[k]}
              for k in keep if k >= (today - timedelta(days=WINDOW_DAYS)).isoformat()]
    points.append({'at': datetime.now(KST), **latest})
    if len(points) < 2:
        return {'name': name, 'level': latest['level'], 'rate': latest['rate'], 'points': len(points),
                'notes': ['경험치 기록이 하루치뿐이라 아직 속도를 계산할 수 없어요.']}
    gain = sum(gained(a, b) for a, b in zip(points, points[1:]))
    span_days = (points[-1]['at'] - points[0]['at']).total_seconds() / 86400
    per_day = gain / span_days if span_days > 0 else 0
    need_now = required(latest)
    left = need_now - latest['exp'] if need_now else None
    days_left = left / per_day if left is not None and per_day > 0 else None
    notes = []
    if any(b['level'] - a['level'] > 1 for a, b in zip(points, points[1:])):
        notes.append('하루에 2레벨 넘게 오른 날은 그 사이 레벨의 경험치를 몰라 조금 적게 잡혔어요.')
    if per_day <= 0:
        notes.append('최근 7일 동안 경험치가 오르지 않았어요.')
    # 그 캐릭터의 재획 기록과 맞춰 소재비 1개당 경험치 %.
    earnings.ensure(store)
    since = points[0]['at'].date().isoformat()
    flasks = sum(r['flasks'] or 0 for r in store.rows(
        "SELECT flasks FROM earnings WHERE kind='hunt' AND character=? AND day>=?", (name, since)))
    return {'name': name, 'level': latest['level'], 'rate': latest['rate'], 'exp': latest['exp'],
            'required': need_now, 'left': left, 'gain': gain, 'span_days': round(span_days, 2),
            'per_day': per_day, 'per_day_percent': per_day / need_now * 100 if need_now else None,
            'days': days_left, 'eta': expected_date(today, days_left),
            'flasks': flasks, 'per_flask_percent': (gain / flasks / need_now * 100) if flasks and need_now else None,
            'points': len(points), 'since': since, 'retrieved_at': now(), 'notes': notes}
