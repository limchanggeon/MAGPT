"""비슷한 유저 장비 통계 — 같은 직업·비슷한 레벨 유저들은 부위마다 무엇을 끼고 어디까지 강화했나.

넥슨 Open API 랭킹(직업별 레벨 순)에서 내 캐릭터 근처 순위의 같은 직업 유저를 고르고, 한 명씩 천천히
장비를 조회해 요약만 저장한다(이름은 저장하지 않는다). 한도에 걸리지 않게:
  - 이 기능은 하루 DAILY_CALLS회까지만 부른다(넥슨 키의 다른 기능 몫을 남긴다).
  - 호출 사이 GAP_SECONDS초 이상 쉰다.
  - 429(한도 초과)를 받으면 그날은 멈춘다.
  - 모은 요약은 KEEP_DAYS일 동안 다시 조회하지 않는다.
"""
import json
import math
import statistics
import threading
import time
from collections import Counter
from datetime import datetime, timedelta

from .core import AppError, KST, now

SCHEMA = '''
CREATE TABLE IF NOT EXISTS peers(
  ocid TEXT PRIMARY KEY, job TEXT NOT NULL, level INTEGER, world_type INTEGER,
  fetched_at TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS peers_job ON peers(job);
'''
TARGET = 'peer_target'        # {job, level, world_type, rank, at} — 마지막으로 고른 기준
QUEUE = 'peer_queue'          # 아직 장비를 보지 않은 후보 [{name, level}]
CALLS = 'peer_calls'          # {day, count, blocked}
DAILY_CALLS = 60
GAP_SECONDS = 3
KEEP_DAYS = 14
LEVEL_BAND = 3                # 후보: 내 레벨 ±3
MAX_QUEUE = 40
MIN_PEERS = 5                 # 이보다 적으면 통계를 보여 주지 않는다
REBOOT_WORLDS = ('에오스', '헬리오스')
SKIP_SLOTS = ('칭호', '안드로이드', '훈장', '뱃지', '포켓 아이템')
GRADES = ('레어', '에픽', '유니크', '레전드리')
NO_STARFORCE_SLOTS = ('보조무기', '엠블렘', '뱃지', '훈장', '포켓 아이템')


def ensure(store):
    with store.db() as db:
        db.executescript(SCHEMA)


def today():
    return datetime.now(KST).date().isoformat()


def yesterday():
    return (datetime.now(KST).date() - timedelta(days=1)).isoformat()


def grade_rank(grade):
    return GRADES.index(grade) + 1 if grade in GRADES else 0


def summarize(equipment):
    """장비 목록(Nexon.equipment_item 형태)에서 통계에 쓸 것만 남긴다."""
    out = {}
    for item in equipment or []:
        slot = item.get('slot')
        if not slot or slot in SKIP_SLOTS or slot in out:
            continue
        out[slot] = {'name': item.get('name'), 'starforce': item.get('starforce'),
                     'potential': item.get('potential_grade'), 'additional': item.get('additional_grade'),
                     'add_grade': (item.get('add_grade') or {}).get('label') or None}
    return out


class Peers:
    """후보 고르기(랭킹 2회)와 천천히 모으기(한 명당 2회). 모으기는 앱이 켜져 있는 동안 뒤에서 돈다."""

    def __init__(self, store, nexon_getter, sleep=time.sleep):
        self.store = store
        self.nexon = nexon_getter          # 앱이 넥슨 객체를 바꿔 끼워도 따라가게 함수로 받는다
        self.sleep = sleep
        self.lock = threading.Lock()
        self.thread = None
        self.last_call = 0.0
        self.error = None
        ensure(store)

    # 호출 한도 -----------------------------------------------------------
    def calls(self):
        return calls(self.store)

    def left(self):
        return left(self.store)

    def get(self, path, query):
        if self.left() <= 0:
            raise AppError('오늘 비슷한 유저 조회 몫을 다 썼어요. 내일 이어서 모읍니다.', 429)
        wait = GAP_SECONDS - (time.monotonic() - self.last_call)
        if wait > 0:
            self.sleep(wait)
        state = self.calls()
        state['count'] += 1
        self.store.set_setting(CALLS, state)
        self.last_call = time.monotonic()
        try:
            return self.nexon().get(path, query)
        except AppError as e:
            if getattr(e, 'upstream', None) == 429:
                state['blocked'] = True
                self.store.set_setting(CALLS, state)
            raise

    # 후보 고르기 ---------------------------------------------------------
    def choose(self, name):
        """내 캐릭터의 직업 랭킹 순위 근처에서 같은 직업·비슷한 레벨 후보를 큐에 넣는다(넥슨 호출 4회)."""
        ocid = self.get('id', {'character_name': name}).get('ocid')
        if not ocid:
            raise AppError('캐릭터 식별자를 확인하지 못했습니다.', 502)
        mine = (self.get('ranking/overall', {'date': yesterday(), 'ocid': ocid}).get('ranking') or [None])[0]
        if not mine or not mine.get('class_name'):
            raise AppError('랭킹에서 이 캐릭터를 찾지 못했어요(랭킹은 전날 기준이라 새 캐릭터는 하루 뒤에 나와요).', 404)
        # 직업 값은 '계열-전직'(전사-히어로). 전직이 없는 직업(렌 등)은 같은 이름을 두 번 쓴다(렌-렌).
        job = f"{mine['class_name']}-{mine.get('sub_class_name') or mine['class_name']}"
        world_type = 1 if mine.get('world_name') in REBOOT_WORLDS else 0
        level = int(mine.get('character_level') or 0)
        # 위 순위는 전체 순위다. 직업을 넣어 직업 안 순위를 다시 받는다.
        in_job = (self.get('ranking/overall', {'date': yesterday(), 'ocid': ocid, 'class': job}).get('ranking') or [None])[0]
        rank = int((in_job or {}).get('ranking') or 1)
        page = max(1, math.ceil(rank / 200))
        found = self.get('ranking/overall', {'date': yesterday(), 'class': job, 'page': page}).get('ranking') or []
        fresh = {r['ocid'] for r in self.store.rows(
            'SELECT ocid FROM peers WHERE job=? AND fetched_at>=?', (job, since()))}
        pool = [r for r in found if isinstance(r, dict) and r.get('character_name') and r.get('character_name') != name
                and abs(int(r.get('character_level') or 0) - level) <= LEVEL_BAND]
        pool.sort(key=lambda r: abs(int(r.get('character_level') or 0) - level))
        queue = [{'name': r['character_name'], 'level': int(r['character_level'])} for r in pool][:MAX_QUEUE]
        self.store.set_setting(TARGET, {'job': job, 'level': level, 'world_type': world_type, 'rank': rank, 'at': now(),
                                        'known': len(fresh)})
        self.store.set_setting(QUEUE, queue)
        return self.status()

    # 모으기 --------------------------------------------------------------
    def step(self):
        """큐에서 한 명의 장비를 본다. 더 할 게 없으면 False."""
        queue = self.store.setting(QUEUE) or []
        target = self.store.setting(TARGET) or {}
        if not queue or not target or self.left() < 2:
            return False
        person = queue.pop(0)
        self.store.set_setting(QUEUE, queue)
        ocid = self.get('id', {'character_name': person['name']}).get('ocid')
        if not ocid:
            return True
        if self.store.rows('SELECT 1 FROM peers WHERE ocid=? AND fetched_at>=?', (ocid, since())):
            return True
        from .adapters import Nexon
        equipped = self.get('character/item-equipment', {'ocid': ocid})
        rows = [Nexon.equipment_item(item, None) for item in (equipped.get('item_equipment') or [])
                if isinstance(item, dict) and item.get('item_name')]
        summary = summarize(rows)
        if summary:
            with self.store.db() as db:
                db.execute('INSERT INTO peers(ocid,job,level,world_type,fetched_at,data) VALUES(?,?,?,?,?,?) '
                           'ON CONFLICT(ocid) DO UPDATE SET job=excluded.job, level=excluded.level, '
                           'fetched_at=excluded.fetched_at, data=excluded.data',
                           (ocid, target['job'], person['level'], target.get('world_type'), now(),
                            json.dumps(summary, ensure_ascii=False)))
        return True

    def run(self):
        try:
            while self.step():
                pass
            self.error = None
        except AppError as e:
            self.error = str(e)
        except Exception as e:              # 모으기 실패가 앱을 멈추게 하지 않는다
            self.error = f'비슷한 유저 조회 중 오류: {e}'

    def start(self):
        """뒤에서 모으기를 시작한다(이미 돌고 있으면 그대로)."""
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            if not (self.store.setting(QUEUE) or []) or self.left() < 2:
                return False
            self.thread = threading.Thread(target=self.run, name='peers', daemon=True)
            self.thread.start()
            return True

    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def status(self):
        return {**status(self.store), 'running': self.running(), 'error': self.error}


def calls(store):
    state = store.setting(CALLS) or {}
    if state.get('day') != today():
        state = {'day': today(), 'count': 0, 'blocked': False}
    return state


def left(store):
    state = calls(store)
    return 0 if state.get('blocked') else max(0, DAILY_CALLS - state['count'])


def since():
    return (datetime.now(KST) - timedelta(days=KEEP_DAYS)).isoformat(timespec='seconds')


def stored(store, target):
    """기준(직업·레벨) 근처로 모아 둔 유저 요약들."""
    if not target:
        return []
    ensure(store)
    rows = store.rows('SELECT level, data FROM peers WHERE job=? AND fetched_at>=? AND level BETWEEN ? AND ?',
                      (target['job'], since(), target['level'] - LEVEL_BAND - 2, target['level'] + LEVEL_BAND + 2))
    return [json.loads(r['data']) for r in rows]


def status(store):
    target = store.setting(TARGET) or None
    return {'target': target, 'queue': len(store.setting(QUEUE) or []), 'collected': len(stored(store, target)),
            'running': False, 'error': None, 'calls_left': left(store), 'daily_calls': DAILY_CALLS, 'min_peers': MIN_PEERS}


def slot_stats(people):
    """부위별: 많이 낀 장비 상위 3, 스타포스 중앙값, 윗잠·아랫잠 등급 비율."""
    slots = {}
    for person in people:
        for slot, item in person.items():
            slots.setdefault(slot, []).append(item)
    out = {}
    for slot, items in slots.items():
        n = len(items)
        names = Counter(i.get('name') for i in items if i.get('name')).most_common(3)
        stars = [i['starforce'] for i in items if isinstance(i.get('starforce'), int)]
        pot = Counter(i.get('potential') for i in items if i.get('potential'))
        add = Counter(i.get('additional') for i in items if i.get('additional'))
        out[slot] = {'count': n,
                     'items': [{'name': k, 'share': round(v / n * 100)} for k, v in names],
                     'starforce_median': statistics.median(stars) if stars else None,
                     'potential': {g: round(pot[g] / n * 100) for g in GRADES if pot[g]},
                     'additional': {g: round(add[g] / n * 100) for g in GRADES if add[g]}}
    return out


def top_grade(shares):
    """절반 이상이 도달한 가장 높은 등급."""
    total = 0
    for g in reversed(GRADES):
        total += shares.get(g, 0)
        if total >= 50:
            return g
    return None


def compare(store, profile, state=None):
    """내 장비와 비슷한 유저 통계를 부위별로 맞대어, 뒤처진 부위를 고른다. 기준 직업과 다른 캐릭터면 비교하지 않는다."""
    target = store.setting(TARGET) or None
    people = stored(store, target)
    job = str((profile or {}).get('job') or '')
    same = bool(target) and (not job or target['job'].split('-', 1)[-1] == job)
    result = {**(state or status(store)), 'ready': same and len(people) >= MIN_PEERS, 'same_job': same,
              'slots': [], 'behind': []}
    if not result['ready']:
        return result
    stats = slot_stats(people)
    mine = summarize((profile or {}).get('equipment'))
    for slot, s in stats.items():
        if s['count'] < max(3, len(people) // 3):
            continue
        me = mine.get(slot) or {}
        row = {'slot': slot, **s, 'mine': me}
        reasons = []
        # 스타포스를 못 하는 부위(보조무기·엠블렘·특수 반지 등)는 스타포스로 비교하지 않는다.
        can_star = slot not in NO_STARFORCE_SLOTS and not (me.get('starforce') in (0, None) and (s['starforce_median'] or 0) <= 5)
        if can_star and isinstance(me.get('starforce'), int) and s['starforce_median'] is not None \
                and me['starforce'] + 2 <= s['starforce_median']:
            reasons.append(f"스타포스 {me['starforce']}성 (비슷한 유저 중앙값 {s['starforce_median']:g}성)")
        # 잠재능력이 아예 없는 장비(시드링 등 특수 반지)는 잠재로 비교하지 않는다(장비를 바꾸는 이야기는 아래 장비 비교가 맡는다).
        common = top_grade(s['potential'])
        if common and me.get('potential') and grade_rank(me.get('potential')) < grade_rank(common):
            reasons.append(f"윗잠 {me.get('potential') or '없음'} (절반 이상이 {common} 이상)")
        common_add = top_grade(s['additional'])
        if common_add and me.get('additional') and grade_rank(me.get('additional')) < grade_rank(common_add):
            reasons.append(f"아랫잠 {me.get('additional') or '없음'} (절반 이상이 {common_add} 이상)")
        top = s['items'][0] if s['items'] else None
        if top and me.get('name') and top['name'] != me['name'] and top['share'] >= 50 \
                and me['name'] not in [i['name'] for i in s['items']]:
            reasons.append(f"장비 {me['name']} (많이 끼는 장비: {top['name']} {top['share']}%)")
        row['behind'] = reasons
        result['slots'].append(row)
        if reasons:
            gap = (s['starforce_median'] or 0) - (me.get('starforce') or 0) if can_star else 0
            result['behind'].append({'slot': slot, 'reasons': reasons, 'score': len(reasons) * 10 + max(0, gap)})
    result['behind'].sort(key=lambda b: -b['score'])
    result['people'] = len(people)
    return result


def facts_text(compared):
    """채팅 모델에 넘길 사실. 앱이 센 값만 적는다."""
    if not compared.get('ready'):
        return ''
    t = compared['target']
    lines = [f"[비슷한 유저 장비 통계] 같은 직업({t['job']}) 레벨 {t['level']}±{LEVEL_BAND} 유저 {compared['people']}명의 "
             f"장비를 넥슨 Open API로 모은 통계다(최근 {KEEP_DAYS}일 안에 조회). 이 밖의 유저 경향은 모른다."]
    for s in compared['slots']:
        parts = [', '.join(f"{i['name']} {i['share']}%" for i in s['items'])]
        if s['starforce_median'] is not None:
            parts.append(f"스타포스 중앙값 {s['starforce_median']:g}성")
        if s['potential']:
            parts.append('윗잠 ' + ', '.join(f'{g} {v}%' for g, v in s['potential'].items()))
        if s['additional']:
            parts.append('아랫잠 ' + ', '.join(f'{g} {v}%' for g, v in s['additional'].items()))
        me = s['mine']
        mine = f" / 내 장비: {me.get('name')} {me.get('starforce') or 0}성 윗잠 {me.get('potential') or '없음'} 아랫잠 {me.get('additional') or '없음'}" if me else ' / 내 장비: 없음'
        lines.append(f"- {s['slot']}: " + ' · '.join(parts) + mine)
    if compared['behind']:
        lines.append('[비슷한 유저보다 뒤처진 부위] (앞일수록 차이가 크다) '
                     + '; '.join(f"{b['slot']}: {', '.join(b['reasons'])}" for b in compared['behind']))
        lines.append('[답변 방법] 목록을 그대로 옮기지 말고, 먼저 손댈 부위 2~3개를 골라 비슷한 유저 비율·중앙값을 이유로 들어 '
                     ('상담하듯 설명한다. 내 장비가 더 좋은 부위가 있으면 짧게 짚는다. 비용·확률·시세는 위 사실에 없으면 말하지 않는다. '
                     '레벨·심볼·유니온·스킬이 달라서, 같은 장비를 껴도 같은 성능이 된다고 단정하지 않는다.'))
    return '\n'.join(lines)
