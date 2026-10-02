"""목표 전투력대 유저 장비 통계 — 같은 직업에서 내가 목표로 하는 전투력(예: 2억 5천만)대 유저들은 부위마다 무엇을 끼나.

넥슨 Open API에는 전투력 랭킹이 없고, 어느 랭킹도 전투력과 잘 맞지 않는다(2026-10-02 실측: 레벨·유니온 순위 모두 같은 쪽에서
0.7억~2.7억이 섞임). 무릉 랭킹은 요즘 기록이 적어(캐논마스터 2명, 섀도어 5명) 직업 대부분에서 쓸 수 없다.
그래서 후보를 넓게 잡고 한 명씩 전투력을 확인한다(이름 해시로 7일 캐시):
  1. 같은 직업 레벨 랭킹(최대 5쪽) + 그 직업의 무릉 기록(있으면 먼저)
  2. 전 직업 레벨 랭킹(최대 5쪽)에서 같은 방어구 계열·같은 주스탯 직업(JOB_FAMILIES) — 인원이 적은 직업은 1과 섞어서 먼저 본다.
목표 ±CP_BAND 안인 사람만 장비를 저장하고, 같은 계열 유저는 표시(__family__)해 무기·보조무기·엠블렘 통계에서 뺀다.
세트 단계표는 비교 유저의 세트 응답에서 배운다(statcalc.learn_sets). 한도에 걸리지 않게:
  - 이 기능은 하루 DAILY_CALLS회까지만 부른다(넥슨 키의 다른 기능 몫을 남긴다).
  - 호출 사이 GAP_SECONDS초 이상 쉰다.
  - 429(한도 초과)를 받으면 그날은 멈춘다.
  - 모은 요약은 KEEP_DAYS일 동안 다시 조회하지 않는다.
"""
import json
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
CREATE TABLE IF NOT EXISTS peer_power(key TEXT PRIMARY KEY, ocid TEXT, cp REAL, at TEXT NOT NULL);
'''
DOJANG_PAGES = 5              # 무릉 랭킹에서 볼 최대 쪽 수(쪽당 200명)
JOB_PAGES = 5                 # 같은 직업 레벨 랭킹에서 볼 쪽 수
FAMILY_PAGES = 5              # 전 직업 레벨 랭킹에서 볼 쪽 수(같은 계열·주스탯만 남긴다)
SMALL_JOB = 300               # 같은 직업 후보가 이보다 적으면 같은 계열 유저를 섞어서 먼저 본다
JOB_SLOTS = ('무기', '보조무기', '엠블렘')   # 직업마다 다른 부위 — 같은 직업 유저로만 통계를 낸다
# 같은 방어구(나이트·메이지·아처·시프·파이렛)를 끼고 주스탯이 같은 직업끼리 묶는다. 제논·데몬어벤져는 따로.
JOB_FAMILIES = (
    ('전사·STR', ('히어로', '팔라딘', '다크나이트', '소울마스터', '미하일', '블래스터', '데몬슬레이어', '아란', '카이저', '아델', '제로', '렌')),
    ('마법사·INT', ('아크메이지(불,독)', '아크메이지(썬,콜)', '비숍', '플레임위자드', '배틀메이지', '에반', '루미너스', '일리움', '라라', '키네시스')),
    ('궁수·DEX', ('보우마스터', '신궁', '패스파인더', '윈드브레이커', '와일드헌터', '메르세데스', '카인')),
    ('도적·LUK', ('나이트로드', '섀도어', '듀얼블레이더', '나이트워커', '팬텀', '카데나', '칼리', '호영')),
    ('해적·STR', ('바이퍼', '캐논마스터', '스트라이커', '은월', '아크')),
    ('해적·DEX', ('캡틴', '메카닉', '엔젤릭버스터')),
)


def job_name(entry):
    """랭킹 항목의 직업 이름(전직 이름, 없으면 계열 이름): ('기사단','소울마스터') → '소울마스터', ('렌','') → '렌'."""
    return entry.get('sub_class_name') or entry.get('class_name') or ''


def family_of(job):
    """직업 → (계열 이름, 같은 계열 직업들). 없으면 (None, ())."""
    for label, jobs in JOB_FAMILIES:
        if job in jobs:
            return label, jobs
    return None, ()
PROBE_COUNT = 6               # 층-전투력 관계를 어림할 표본 수
CP_BAND = 0.15                # 목표 전투력 ±15%
PROBES = 7                    # 목표 전투력 쪽 찾기에서 볼 랭킹 쪽 수(쪽마다 2명 확인)
DAILY_SETTING = 'peer_daily_calls'
TARGET = 'peer_target'        # {job, level, world_type, rank, at} — 마지막으로 고른 기준
QUEUE = 'peer_queue'          # 아직 장비를 보지 않은 후보 [{name, level}]
CALLS = 'peer_calls'          # {day, count, blocked}
DAILY_CALLS = 120             # 넥슨 개발 키 하루 한도(약 1,000회로 알려짐, 미확인) 중 이 기능 몫. 설정 peer_daily_calls로 바꿀 수 있다.
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
    with store.schema('peers') as needed:
        if not needed:
            return
        with store.db() as db:
            db.executescript(SCHEMA)
            columns = [r['name'] for r in db.execute('PRAGMA table_info(peers)')]
            if 'cp' not in columns:
                db.execute('ALTER TABLE peers ADD COLUMN cp REAL')
            if 'family' not in columns:
                db.execute('ALTER TABLE peers ADD COLUMN family INTEGER DEFAULT 0')


def parse_cp(text):
    """'2억5천', '2억 5000', '2.5억', '250000000' → 전투력. 억 뒤의 천·숫자는 만 단위로 본다(2억5천 = 2억 5천만)."""
    import re
    from .prices import parse_price
    s = str(text or '').replace(',', '').replace(' ', '')
    m = re.fullmatch(r'(\d+(?:\.\d+)?)억(?:(\d+)(천)?만?)?', s)
    if m:
        value = float(m[1]) * 1e8
        if m[2]:
            value += int(m[2]) * (1000 if m[3] else 1) * 1e4
        return value
    value = parse_price(s)
    return value if value and value >= 1e6 else None


def target_floor(points, cp):
    """(층, 전투력) 표본으로 목표 전투력의 층을 어림한다. 층 순으로 이웃한 두 점 사이를 직선으로 잇고, 범위 밖이면 끝 층."""
    pts = sorted(points)
    if not pts:
        return 0
    if len(pts) == 1:
        return pts[0][0]
    best = min(pts, key=lambda p: abs(p[1] - cp))[0]
    for (f1, c1), (f2, c2) in zip(pts, pts[1:]):
        if min(c1, c2) <= cp <= max(c1, c2) and c1 != c2:
            best = f1 + (cp - c1) * (f2 - f1) / (c2 - c1)
    if cp > max(c for _, c in pts):
        best = max(pts, key=lambda p: p[1])[0]
    return round(best)


def in_band(cp, target):
    return cp is not None and target and abs(cp - target) <= target * CP_BAND


def today():
    return datetime.now(KST).date().isoformat()


def yesterday():
    return (datetime.now(KST).date() - timedelta(days=1)).isoformat()


def grade_rank(grade):
    return GRADES.index(grade) + 1 if grade in GRADES else 0


SIM_OPTIONS = ('str', 'dex', 'int', 'luk', 'attack_power', 'magic_power', 'all_stat', 'damage', 'boss_damage', 'ignore_monster_armor')


def sim_item(raw):
    """교체 시뮬레이션(statcalc.swap)에 필요한 만큼만 남긴 장비 원본. 사람을 알 수 있는 정보는 없다."""
    total = raw.get('item_total_option') or {}
    out = {'item_name': raw.get('item_name'), 'item_equipment_slot': raw.get('item_equipment_slot'),
           'starforce': raw.get('starforce'), 'soul_option': raw.get('soul_option'),
           'item_total_option': {k: total.get(k) for k in SIM_OPTIONS if total.get(k) not in (None, '0', 0)}}
    for prefix in ('potential_option_', 'additional_potential_option_'):
        for i in (1, 2, 3):
            if raw.get(prefix + str(i)):
                out[prefix + str(i)] = raw[prefix + str(i)]
    return out


def summarize(equipment, raw=None):
    """장비 목록(Nexon.equipment_item 형태)에서 통계에 쓸 것만 남긴다. raw(API 원본 목록)를 주면 시뮬레이션용 옵션도 붙인다."""
    originals = {}
    for r in raw or []:
        if isinstance(r, dict) and r.get('item_equipment_slot') and r['item_equipment_slot'] not in originals:
            originals[r['item_equipment_slot']] = sim_item(r)
    out = {}
    for item in equipment or []:
        slot = item.get('slot')
        if not slot or slot in SKIP_SLOTS or slot in out:
            continue
        out[slot] = {'name': item.get('name'), 'starforce': item.get('starforce'),
                     'potential': item.get('potential_grade'), 'additional': item.get('additional_grade'),
                     'add_grade': (item.get('add_grade') or {}).get('label') or None}
        if slot in originals and originals[slot]['item_name'] == item.get('name'):
            out[slot]['item'] = originals[slot]
    return out


class Peers:
    """후보 고르기(랭킹 2회)와 천천히 모으기(한 명당 2회). 모으기는 앱이 켜져 있는 동안 뒤에서 돈다."""

    def __init__(self, store, nexon_getter, sleep=time.sleep):
        self.store = store
        self.nexon = nexon_getter          # 앱이 넥슨 객체를 바꿔 끼워도 따라가게 함수로 받는다
        self.sleep = sleep
        self.lock = threading.Lock()
        self.call_lock = threading.Lock()
        self.collection_lock = threading.RLock()
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
        # 화면의 '다시 찾기'와 수집 스레드가 호출 몫·최소 간격을 동시에 갱신하지 않게 한다.
        with self.call_lock:
            return self._get(path, query)

    def _get(self, path, query):
        if self.left() <= 0:
            raise AppError('오늘 목표 전투력대 유저 조회 몫을 다 썼어요. 내일 이어서 모읍니다.', 429)
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
    def power(self, name):
        """이름 → (ocid, 전투력). 7일 안에 확인한 사람은 캐시(이름 해시)에서, 아니면 2회."""
        import hashlib
        key = hashlib.sha256(name.encode()).hexdigest()
        hit = self.store.rows('SELECT ocid, cp FROM peer_power WHERE key=? AND at>=?',
                              (key, (datetime.now(KST) - timedelta(days=7)).isoformat(timespec='seconds')))
        if hit:
            return hit[0]['ocid'], hit[0]['cp']
        ocid = self.get('id', {'character_name': name}).get('ocid')
        if not ocid:
            return None, None
        final = self.get('character/stat', {'ocid': ocid}).get('final_stat') or []
        self.finals = getattr(self, 'finals', {})
        self.finals[ocid] = final                 # 장비를 받을 때 투력 기준 프리셋을 고르는 데 쓴다(추가 호출 없음)
        cp = next((x.get('stat_value') for x in final if x.get('stat_name') == '전투력'), None)
        try:
            cp = float(str(cp).replace(',', '')) if cp is not None else None
        except ValueError:
            cp = None
        with self.store.db() as db:
            db.execute('INSERT OR REPLACE INTO peer_power(key, ocid, cp, at) VALUES(?,?,?,?)', (key, ocid, cp, now()))
        return ocid, cp

    def choose(self, name, cp):
        """목표 전투력대 후보를 큐에 넣는다: 같은 직업 랭킹(+무릉 기록) + 같은 계열·주스탯 직업(약 10~15회)."""
        with self.collection_lock:
            return self._choose(name, cp)

    def _choose(self, name, cp):
        if not cp or cp < 1e6:
            raise AppError("목표 전투력을 '2억5천'처럼 적어 주세요.")
        ocid = self.get('id', {'character_name': name}).get('ocid')
        if not ocid:
            raise AppError('캐릭터 식별자를 확인하지 못했습니다.', 502)
        mine = (self.get('ranking/overall', {'date': yesterday(), 'ocid': ocid}).get('ranking') or [None])[0]
        if not mine or not mine.get('class_name'):
            raise AppError('랭킹에서 이 캐릭터를 찾지 못했어요(랭킹은 전날 기준이라 새 캐릭터는 하루 뒤에 나와요).', 404)
        job = f"{mine['class_name']}-{mine.get('sub_class_name') or mine['class_name']}"
        my_job = job_name(mine)
        seen = {name}

        def take(rows, **extra):
            out = []
            for r in rows:
                n = r.get('character_name')
                if n and n not in seen:
                    seen.add(n)
                    out.append({'name': n, 'level': int(r.get('character_level') or 0), **extra})
            return out

        # 1) 같은 직업: 무릉 기록(있으면, 층 높은 순) → 레벨 랭킹
        dojang = []
        for page in range(1, DOJANG_PAGES + 1):
            rows = self.get('ranking/dojang', {'date': yesterday(), 'difficulty': 1, 'class': job, 'page': page}).get('ranking') or []
            dojang += sorted(rows, key=lambda r: -int(r.get('dojang_floor') or 0))
            if len(rows) < 200:
                break
        same = take(dojang)
        for page in range(1, JOB_PAGES + 1):
            rows = self.get('ranking/overall', {'date': yesterday(), 'class': job, 'page': page}).get('ranking') or []
            same += take(rows)
            if len(rows) < 200:
                break
        # 2) 같은 계열·주스탯 직업(전 직업 레벨 랭킹에서 직업 이름으로 거른다)
        label, jobs = family_of(my_job)
        family = []
        if jobs:
            for page in range(1, FAMILY_PAGES + 1):
                rows = self.get('ranking/overall', {'date': yesterday(), 'page': page}).get('ranking') or []
                family += take([r for r in rows if job_name(r) in jobs and job_name(r) != my_job], family=True)
                if len(rows) < 200:
                    break
        if len(same) < SMALL_JOB and family:
            queue = []                                   # 인원이 적은 직업: 같은 직업과 같은 계열을 번갈아
            for a, b in zip(same, family):
                queue += [a, b]
            longer = same if len(same) > len(family) else family
            queue += longer[min(len(same), len(family)):]
        else:
            queue = same + family
        if not queue:
            raise AppError('이 직업의 랭킹 후보를 찾지 못했어요.', 404)
        self.store.set_setting(TARGET, {'job': job, 'cp': cp, 'band': CP_BAND, 'level': int(mine.get('character_level') or 0),
                                        'family': label, 'pool': len(same), 'family_pool': len(family),
                                        'dojang': len(dojang), 'screened': 0, 'matched': 0, 'at': now()})
        self.store.set_setting(QUEUE, queue[:MAX_QUEUE * 30])
        return self.status()

    # 모으기 --------------------------------------------------------------
    def step(self):
        """큐에서 한 명을 확인한다: 전투력이 목표대면 장비(필요하면 세트 단계표도)를 저장. 더 할 게 없으면 False."""
        with self.collection_lock:
            return self._step()

    def _step(self):
        from . import statcalc
        queue = self.store.setting(QUEUE) or []
        target = self.store.setting(TARGET) or {}
        if not queue or not target.get('cp') or self.left() < 3:
            return False
        if len(stored(self.store, target)) >= MAX_QUEUE:
            self.store.set_setting(QUEUE, [])          # 충분히 모였다
            return False
        person = queue.pop(0)
        self.store.set_setting(QUEUE, queue)
        ocid, cp = person.get('ocid'), person.get('cp')
        if not cp:
            ocid, cp = self.power(person['name'])
            target['screened'] = int(target.get('screened') or 0) + 1
        if not ocid or not in_band(cp, target['cp']):
            self.store.set_setting(TARGET, target)
            return True
        target['matched'] = int(target.get('matched') or 0) + 1
        self.store.set_setting(TARGET, target)
        if self.store.rows('SELECT 1 FROM peers WHERE ocid=? AND fetched_at>=?', (ocid, since())):
            return True
        from .adapters import Nexon
        equipped = self.get('character/item-equipment', {'ocid': ocid})
        originals = [item for item in best_preset(equipped, getattr(self, 'finals', {}).pop(ocid, None))
                     if isinstance(item, dict) and item.get('item_name')]
        if statcalc.unknown_sets(originals, self.store.setting(statcalc.SET_TABLES) or {}):
            statcalc.learn_sets(self.store, self.get('character/set-effect', {'ocid': ocid}))
        rows = [Nexon.equipment_item(item, None) for item in originals]
        summary = summarize(rows, originals)
        if summary:
            with self.store.db() as db:
                db.execute('INSERT INTO peers(ocid,job,level,world_type,fetched_at,data,cp,family) VALUES(?,?,?,?,?,?,?,?) '
                           'ON CONFLICT(ocid) DO UPDATE SET job=excluded.job, level=excluded.level, '
                           'fetched_at=excluded.fetched_at, data=excluded.data, cp=excluded.cp, family=excluded.family',
                           (ocid, target['job'], person.get('level'), 0, now(), json.dumps(summary, ensure_ascii=False), cp,
                            1 if person.get('family') else 0))
        return True

    def restart(self, name, cp):
        """목표 전투력을 바꿔 뒤에서 다시 찾고 모은다(대화에서 목표를 말했을 때). 이미 돌고 있으면 그 뒤에 이어서."""
        def job():
            try:
                self.choose(name, cp)
            except AppError as e:
                self.error = str(e)
                return
            self.run()
        with self.lock:
            if self.thread and self.thread.is_alive():
                self.error = '이전 모으기가 끝나면 새 목표로 다시 찾아 주세요.'
                return False
            self.thread = threading.Thread(target=job, name='peers-restart', daemon=True)
            self.thread.start()
            return True

    def run(self):
        try:
            while self.step():
                pass
            self.error = None
        except AppError as e:
            self.error = str(e)
        except Exception as e:              # 모으기 실패가 앱을 멈추게 하지 않는다
            self.error = f'목표 전투력대 유저 조회 중 오류: {e}'

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


def daily_calls(store):
    try:
        return int(store.setting(DAILY_SETTING) or DAILY_CALLS)
    except (TypeError, ValueError):
        return DAILY_CALLS


def left(store):
    state = calls(store)
    return 0 if state.get('blocked') else max(0, daily_calls(store) - state['count'])


def since():
    return (datetime.now(KST) - timedelta(days=KEEP_DAYS)).isoformat(timespec='seconds')


def stored(store, target):
    """목표 전투력대(직업 같고 ±CP_BAND)로 모아 둔 유저 요약들. 전투력을 모르는 예전 기록은 쓰지 않는다."""
    if not target or not target.get('cp'):
        return []
    ensure(store)
    cp = target['cp']
    rows = store.rows('SELECT data, family FROM peers WHERE job=? AND fetched_at>=? AND cp BETWEEN ? AND ?',
                      (target['job'], since(), cp * (1 - CP_BAND), cp * (1 + CP_BAND)))
    out = []
    for r in rows:
        person = json.loads(r['data'])
        if r['family']:
            person['__family__'] = True       # 같은 계열 유저 — 직업 부위(무기 등) 통계에서 뺀다
        out.append(person)
    return out


def slots_of(person):
    """사람의 부위별 장비. 같은 계열 유저는 직업마다 다른 부위(무기·보조무기·엠블렘)를 뺀다."""
    family = person.get('__family__')
    return {s: v for s, v in person.items() if not s.startswith('__') and not (family and s in JOB_SLOTS)}


def status(store):
    target = store.setting(TARGET) or None
    return {'target': target, 'queue': len(store.setting(QUEUE) or []), 'collected': len(stored(store, target)),
            'running': False, 'error': None, 'calls_left': left(store), 'daily_calls': daily_calls(store), 'min_peers': MIN_PEERS}


def slot_stats(people):
    """부위별: 많이 낀 장비 상위 3, 스타포스 중앙값, 윗잠·아랫잠 등급 비율."""
    slots = {}
    for person in people:
        for slot, item in slots_of(person).items():
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


def candidate(people, slot, name):
    """목표 전투력대 유저가 낀 그 장비 중 스타포스가 중앙인 한 벌(옵션이 저장된 것만)."""
    pool = [p[slot]['item'] for p in people if slot in slots_of(p) and p[slot].get('name') == name and p[slot].get('item')]
    if not pool:
        return None
    pool.sort(key=lambda i: int(i.get('starforce') or 0))
    return pool[len(pool) // 2]


# 자리가 여럿인 부위. 같은 장비는 두 자리에 낄 수 없어(고유 장착) 묶어서 서로 다른 장비를 배정한다.
SLOT_GROUPS = {'반지': ('반지1', '반지2', '반지3', '반지4'), '펜던트': ('펜던트', '펜던트2')}


def group_of(slot):
    return next((g for g in SLOT_GROUPS.values() if slot in g), (slot,))


def simulate(people, stats, ledger):
    """부위마다 '많이 끼는 장비(중앙 수준 한 벌)로 바꾸면' 스탯공격력·보스 기준 변화 범위.

    반지·펜던트처럼 자리가 여럿인 부위는 자리×후보 장비를 모두 계산해 효과가 큰 순서로, 한 자리에 한 장비·한 장비는 한 자리만 배정한다.
    다른 자리에 이미 낀 장비는 후보에서 뺀다(같은 자리의 같은 장비를 더 좋은 옵션으로 맞추는 것은 된다)."""
    from . import statcalc
    mine = {}
    for item in getattr(ledger, 'items', None) or []:
        mine.setdefault(item.get('item_equipment_slot'), item)
    out, done = {}, set()
    for slot in stats:
        group = group_of(slot)
        if group in done:
            continue
        done.add(group)
        slots = [s for s in group if s in mine and s in stats and stats[s]['items']
                 and not statcalc.is_lucky(mine[s].get('item_name'))]   # 제네시스 무기는 바꾸지 않는다
        names = []
        for s in slots:
            names += [i['name'] for i in stats[s]['items'][:3 if len(group) > 1 else 1]]
        names = list(dict.fromkeys(names))
        pairs = []
        for s in slots:
            worn_elsewhere = {mine[o].get('item_name') for o in group if o != s and o in mine}
            for name in names:
                if name in worn_elsewhere:
                    continue
                pick = candidate(people, s, name) or next((candidate(people, o, name) for o in group if candidate(people, o, name)), None)
                if not pick:
                    continue
                result = statcalc.swap(ledger, mine[s], pick)
                pairs.append((result['boss_range'][1], s, name, pick, result))
        used_slots, used_names = set(), set()
        for _, s, name, pick, result in sorted(pairs, key=lambda x: -x[0]):
            if s in used_slots or name in used_names:
                continue
            used_slots.add(s)
            used_names.add(name)
            out[s] = {'item': pick['item_name'], 'starforce': int(pick.get('starforce') or 0),
                      'same_item': pick['item_name'] == mine[s].get('item_name'),
                      'potential': [pick[k] for k in ('potential_option_1', 'potential_option_2', 'potential_option_3') if pick.get(k)],
                      'range': result['range'], 'boss_range': result['boss_range'], 'sets': result['sets'],
                      'unknown': result['unknown'], 'defense': result['defense']}
    return out


SIMULATION_NOTE = ('보스 기준은 방어율 300% 보스·크리티컬 항상 발동으로 본 상대값(전투력 아님). 설명되지 않는 스탯(헥사 스탯 등)을 '
                   '고정치로 볼 때와 %로 볼 때의 두 값을 범위로 보인다. 교체 장비는 목표 전투력대 유저가 낀 그 장비 중 스타포스 중앙인 한 벌. '
                   '각 줄은 그 부위 하나만 바꿀 때다 — 여러 부위를 함께 바꾸면 세트 효과가 겹쳐 줄들의 합과 다르다. 제네시스 무기는 바꾸지 않는 것으로 본다.')


def compare(store, profile, state=None, ledger=None):
    """내 장비와 목표 전투력대 유저 통계를 부위별로 맞대어, 뒤처진 부위를 고른다. 기준 직업과 다른 캐릭터면 비교하지 않는다."""
    target = store.setting(TARGET) or None
    people = stored(store, target)
    job = str((profile or {}).get('job') or '')
    same = bool(target) and (not job or target['job'].split('-', 1)[-1] == job)
    result = {**(state or status(store)), 'ready': same and len(people) >= MIN_PEERS, 'same_job': same,
              'slots': [], 'behind': []}
    if not result['ready']:
        return result
    stats = slot_stats(people)
    rows, result['preset'], result['applied_preset'] = profile_rows(profile, ledger)
    mine = summarize(rows)
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
            reasons.append(f"스타포스 {me['starforce']}성 (목표 전투력대 유저 중앙값 {s['starforce_median']:g}성)")
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
    result['people_family'] = sum(1 for x in people if x.get('__family__'))
    result['people_exact'] = result['people'] - result['people_family']
    result['simulated'] = False
    if ledger is not None:
        sims = simulate(people, stats, ledger)
        for row in result['slots']:
            row['simulation'] = sims.get(row['slot'])
        for b in result['behind']:
            b['simulation'] = sims.get(b['slot'])
        result['simulated'] = bool(sims)
        result['simulation_note'] = SIMULATION_NOTE
        # 시뮬레이션상 손해인 교체는 권하지 않는다: '많이 끼는 장비' 이유를 빼고, 내 장비가 더 나은 부위로 따로 둔다.
        result['upgrades'] = sorted(({'slot': k, **v} for k, v in sims.items() if v['boss_range'][0] > 0),
                                    key=lambda x: -x['boss_range'][0])
        result['ahead'] = sorted(({'slot': k, **v} for k, v in sims.items() if v['boss_range'][1] < 0),
                                 key=lambda x: x['boss_range'][1])
        worse = {x['slot'] for x in result['ahead']}
        kept = []
        for b in result['behind']:
            if b['slot'] in worse:
                b['reasons'] = [r for r in b['reasons'] if not r.startswith('장비 ')]
            if b['reasons']:
                kept.append(b)
        result['behind'] = kept
    return result


def span(r):
    """'+1.74%~+1.98%' — 두 끝 모두에 %를 붙인다(대화 답의 수치 검사가 %가 붙은 값만 사실로 인정한다)."""
    return f"{r[0]:+.2f}%~{r[1]:+.2f}%" if r[0] != r[1] else f"{r[0]:+.2f}%"


def facts_text(compared):
    """채팅 모델에 넘길 사실. 앱이 센 값만 적는다."""
    if not compared.get('ready'):
        return ''
    t = compared['target']
    who = (f"같은 직업({t['job']}) {compared.get('people_exact', compared['people'])}명"
           + (f" + 같은 방어구·주스탯 계열({t.get('family')}) {compared['people_family']}명(무기·보조무기·엠블렘은 같은 직업만)"
              if compared.get('people_family') else ''))
    preset_note = (f"[기준 프리셋] 내 장비와 비교 유저 장비는 전투력이 가장 높은(보스) 프리셋 기준이다. 내 기준 프리셋 {compared.get('preset')}번"
                   + (f"(지금 게임에서 적용 중인 것은 {compared.get('applied_preset')}번 — 사냥 세팅으로 보임)" if compared.get('preset') and compared.get('applied_preset')
                      and str(compared['preset']) != str(compared['applied_preset']) else '') + '.')
    lines = [preset_note, f"[목표 전투력대 유저 장비 통계] 전투력 {t['cp'] / 1e8:.2f}억 ±{round(CP_BAND * 100)}%인 {who}, 모두 {compared['people']}명의 "
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
    def sim_line(sim):
        extra = (' · 세트 ' + ', '.join(sim['sets'])) if sim['sets'] else ''
        unknown = (' · 모름: ' + '; '.join(sim['unknown'])) if sim['unknown'] else ''
        what = (f"같은 {sim['item']}를 목표 전투력대 유저 수준({sim['starforce']}성, {', '.join(sim['potential']) or '잠재 없음'})으로 맞추면"
                if sim.get('same_item') else
                f"{sim['item']} {sim['starforce']}성({', '.join(sim['potential']) or '잠재 없음'})으로 바꾸면")
        return (f"- {sim['slot']}: {what} "
                f"스탯공격력 {span(sim['range'])}, 보스 기준 {span(sim['boss_range'])}{extra}{unknown}")
    head = []
    if compared.get('simulated'):
        head.append('[교체 시뮬레이션] 앱이 계산한 값이다. ' + SIMULATION_NOTE)
        head.append('[바꾸면 좋아지는 부위] (보스 기준 이득이 큰 순)' if compared['upgrades'] else '[바꾸면 좋아지는 부위] 없음')
        head += [sim_line(s) for s in compared['upgrades']]
        if compared['ahead']:
            head.append('[바꾸면 오히려 손해인 부위] 목표 전투력대 유저가 많이 끼는 장비보다 지금 장비가 낫다. 이 부위의 교체는 권하지 말 것.')
            head += [sim_line(s) for s in compared['ahead']]
    lines[1:1] = head
    if compared['behind']:
        lines.append('[목표 전투력대 유저보다 뒤처진 부위] (앞일수록 차이가 크다) '
                     + '; '.join(f"{b['slot']}: {', '.join(b['reasons'])}" for b in compared['behind']))
    if compared.get('simulated'):
        lines.append('[답변 방법] 교체 시뮬레이션을 가장 먼저 근거로 삼는다. \'바꾸면 좋아지는 부위\'에서 먼저 할 2~3개를 보스 기준 변화율과 함께 권하고, '
                     '\'손해인 부위\'는 지금 장비를 유지하라고 짧게 말한다(목표 전투력대 유저가 많이 낀다는 이유만으로 권하지 않는다). '
                     '뒤처진 부위(스타포스·잠재 등급)는 강화 방향으로 덧붙인다. 비용·확률·시세는 위 사실에 없으면 말하지 않는다. '
                     '레벨·심볼·유니온·스킬이 달라서, 같은 장비를 껴도 같은 성능이 된다고 단정하지 않는다.')
    elif compared['behind']:
        lines.append('[답변 방법] 목록을 그대로 옮기지 말고, 먼저 손댈 부위 2~3개를 골라 목표 전투력대 유저 비율·중앙값을 이유로 들어 '
                     '상담하듯 설명한다. 내 장비가 더 좋은 부위가 있으면 짧게 짚는다. 비용·확률·시세는 위 사실에 없으면 말하지 않는다. '
                     '레벨·심볼·유니온·스킬이 달라서, 같은 장비를 껴도 같은 성능이 된다고 단정하지 않는다.')
    return '\n'.join(lines)


def best_preset(equipped, final=None):
    """비교 유저의 기준 장비: 전투력(보스 기준 추정)이 가장 높은 프리셋. 스탯을 모르면 드롭·메획 줄이 가장 적은 프리셋."""
    from . import statcalc
    current = equipped.get('item_equipment') or []
    if final:
        empty = {p: {} for p in statcalc.PATHS}
        try:
            ledger = statcalc.build({**empty, 'character/stat': {'final_stat': final}, 'character/item-equipment': equipped, 'skills': []})
            return ledger.items or current
        except Exception:
            pass
    presets = {n: equipped.get(f'item_equipment_preset_{n}') or [] for n in (1, 2, 3)}
    presets = {n: rows for n, rows in presets.items() if rows}
    if not presets:
        return current
    applied = equipped.get('preset_no')
    no = min(presets, key=lambda n: (statcalc.hunting_lines(presets[n]), n != applied))
    return presets[no]


def profile_rows(profile, ledger=None):
    """내 장비 중 비교·상담에 쓸 프리셋(화면용으로 바꾼 장비). (장비, 기준 프리셋 번호, 적용 중 번호)."""
    from . import statcalc
    profile = profile or {}
    applied = profile.get('equipment_preset')
    presets = profile.get('equipment_presets') or {}
    no = getattr(ledger, 'preset', None) if ledger is not None else None
    if no is None and presets:
        no = int(min(presets, key=lambda n: (statcalc.hunting_lines(presets[n]), str(n) != str(applied))))
    rows = presets.get(str(no)) if no is not None else None
    return (rows or profile.get('equipment') or []), no or applied, applied
