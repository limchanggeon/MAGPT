import hashlib
import json
import math
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

KST = timezone(timedelta(hours=9))

def now():
    return datetime.now(KST).isoformat(timespec='seconds')

def identifier():
    return uuid4().hex

class AppError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status

def required(data, key, limit=1000):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise AppError(f'{key}: 1~{limit}자의 값을 입력해 주세요.')
    return value.strip()

def number(data, key, low=0, high=1e15):
    try:
        if isinstance(data.get(key), bool):
            raise ValueError()
        value = float(data[key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError()
        return value
    except (KeyError, TypeError, ValueError):
        raise AppError(f'{key}: {low}~{high} 범위의 숫자를 입력해 주세요.')

def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        return result if result.tzinfo else result.replace(tzinfo=KST)
    except (TypeError, ValueError):
        raise AppError('날짜 형식을 확인해 주세요. 예: 2026-09-28')

TERMS = [
    {'term': '기대값', 'aliases': ['기댓값'], 'meaning': '비용·횟수·결과의 평균입니다. 성공 보장 금액이나 중앙값과는 다릅니다.', 'question': '어떤 대상의 기대 비용 또는 기대 횟수를 계산할까요?'},
    {'term': '환산', 'aliases': [], 'meaning': '지표, 계산 서비스 또는 계산 행위를 가리킬 수 있습니다. 공식 전투력·주스탯과 동일시하지 않습니다.', 'question': '어떤 환산 지표나 서비스를 말씀하시나요? 버전과 입력 조건을 알려 주세요. 커뮤니티 계산기 "주스탯 환산" 기준이라면 계산 화면의 주스탯 환산을 사용하세요. 넥슨 공식 수치는 아닙니다.'},
    {'term': '대장장이', 'aliases': [], 'meaning': '강화 의뢰 문맥의 커뮤니티 용례가 있습니다. NPC를 의미하는 경우와 구분해야 합니다.', 'question': '강화 의뢰를 뜻하시나요, 게임 속 NPC를 뜻하시나요?'}
]

def normalize(text):
    from .language import expand
    text = expand(text)
    for term in TERMS:
        for alias in term['aliases']:
            text = text.replace(alias, term['term'])
    return text

class Store:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.folder / 'mepiti.sqlite3'
        self._schema_lock = threading.Lock()
        self._schemas = set()
        self._operation_guard = threading.Lock()
        self._operations = {}
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, title TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE, role TEXT, payload TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS characters(id TEXT PRIMARY KEY, name TEXT UNIQUE, goal TEXT, budget REAL, main INTEGER, created_at TEXT);
            CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, character_id TEXT REFERENCES characters(id) ON DELETE CASCADE, data TEXT, retrieved_at TEXT);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, title TEXT, body TEXT, metadata TEXT);
            CREATE TABLE IF NOT EXISTS prices(id TEXT PRIMARY KEY, item TEXT NOT NULL, add_grade INTEGER, potential TEXT, price REAL NOT NULL, source TEXT NOT NULL, note TEXT, recorded_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS prices_item ON prices(item);
            CREATE INDEX IF NOT EXISTS prices_item_time ON prices(item, recorded_at);
            CREATE INDEX IF NOT EXISTS prices_item_grade_time ON prices(item, add_grade, recorded_at);
            CREATE INDEX IF NOT EXISTS prices_time_source ON prices(recorded_at, source);
            CREATE INDEX IF NOT EXISTS snapshots_character_time ON snapshots(character_id, retrieved_at DESC);
            CREATE INDEX IF NOT EXISTS snapshots_time ON snapshots(retrieved_at);
            CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id);
            ''')
            # 장비를 주제로 한 대화. 예전 DB에는 이 열이 없어 추가만 한다(기존 대화는 그대로).
            if 'topic' not in [r['name'] for r in db.execute('PRAGMA table_info(sessions)')]:
                db.execute('ALTER TABLE sessions ADD COLUMN topic TEXT')
        os.chmod(self.path, 0o600)
        self.purge_expired()

    @contextmanager
    def db(self):
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def rows(self, query, args=()):
        with self.db() as db:
            return [dict(r) for r in db.execute(query, args)]

    @contextmanager
    def schema(self, name):
        """각 표의 호환성 작업은 Store마다 한 번. 실패하면 다음 요청에서 재시도한다."""
        with self._schema_lock:
            if name in self._schemas:
                yield False
            else:
                yield True
                self._schemas.add(name)

    @contextmanager
    def operation(self, name):
        """외부 조회 후 저장하는 작업을 같은 대상끼리만 직렬화한다. 끝난 잠금은 제거한다."""
        with self._operation_guard:
            entry = self._operations.setdefault(name, [threading.RLock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._operation_guard:
                entry[1] -= 1
                if not entry[1]:
                    del self._operations[name]

    def setting(self, key, default=''):
        rows = self.rows('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(rows[0]['value']) if rows else default

    def set_setting(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(value)))

    def purge_expired(self):
        # Raw Nexon snapshots are conservatively removed after 30 days.
        cutoff = (datetime.now(KST) - timedelta(days=30)).isoformat()
        with self.db() as db:
            db.execute('DELETE FROM snapshots WHERE retrieved_at < ?', (cutoff,))

    def characters(self, include_snapshots=True):
        if not include_snapshots:
            return self.rows('SELECT * FROM characters ORDER BY main DESC, created_at')
        self.purge_expired()
        with self.db() as db:
            chars = [dict(r) for r in db.execute('SELECT * FROM characters ORDER BY main DESC, created_at')]
            snapshots_by_character = {}
            # CROSS JOIN으로 캐릭터를 먼저 읽어 각 캐릭터의 최신 두 rowid만 찾는다.
            # 일반 JOIN은 SQLite가 스냅샷 전체를 먼저 순회하도록 계획할 수 있다.
            for row in db.execute('''SELECT s.* FROM characters c CROSS JOIN snapshots s
                WHERE s.rowid IN (SELECT rowid FROM snapshots WHERE character_id=c.id
                                 ORDER BY retrieved_at DESC, rowid DESC LIMIT 2)
                ORDER BY s.retrieved_at DESC, s.rowid DESC'''):
                snapshot = dict(row)
                snapshot['data'] = json.loads(snapshot['data'])
                snapshots_by_character.setdefault(snapshot['character_id'], []).append(snapshot)
        for c in chars:
            snapshots = snapshots_by_character.get(c['id'], [])
            c['snapshots'] = snapshots
            c['changes'] = {}
            if len(snapshots) == 2:
                a, b = [s['data'] for s in snapshots]
                for key in ('level', 'combat_power', 'stat'):
                    if isinstance(a.get(key), (int, float)) and isinstance(b.get(key), (int, float)):
                        c['changes'][key] = a[key] - b[key]
        return chars

    def character_save(self, data):
        name = required(data, 'name', 30)
        goal = str(data.get('goal', ''))[:500]
        budget = number(data, 'budget')
        cid = data.get('id') or identifier()
        with self.db() as db:
            existing = db.execute('SELECT id FROM characters WHERE id=?', (cid,)).fetchone()
            if data.get('id') and not existing:
                raise AppError('캐릭터를 찾을 수 없습니다.', 404)
            main = bool(data.get('main')) or not db.execute('SELECT 1 FROM characters').fetchone()
            if main:
                db.execute('UPDATE characters SET main=0')
            try:
                db.execute('''INSERT INTO characters VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,goal=excluded.goal,budget=excluded.budget,main=excluded.main''',
                    (cid, name, goal, budget, int(main), now()))
            except sqlite3.IntegrityError:
                raise AppError('이미 등록된 캐릭터 이름입니다.')
            if not db.execute('SELECT 1 FROM characters WHERE main=1').fetchone():
                db.execute('UPDATE characters SET main=1 WHERE id=?', (cid,))
        return {'id': cid}

    def import_document(self, data):
        title, body = required(data, 'title', 200), required(data, 'body', 100000)
        meta = data.get('metadata', {})
        if not isinstance(meta, dict):
            raise AppError('metadata는 객체여야 합니다.')
        url = urlparse(required(meta, 'source_url', 2000))
        if url.scheme != 'https' or not url.hostname or url.username:
            raise AppError('출처는 유효한 HTTPS 주소여야 합니다.')
        kind = meta.get('source_type', 'community')
        if kind not in ('official', 'community'):
            raise AppError('출처 유형을 확인해 주세요.')
        if kind == 'official' and not (url.hostname == 'nexon.com' or url.hostname.endswith('.nexon.com')):
            raise AppError('공식 자료는 nexon.com 출처만 등록할 수 있습니다.')
        clean = {k: meta.get(k) for k in ('source_url','published_at','modified_at','effective_from','effective_to','version','region','server_type','valid_until','topic')}
        for key in ('published_at','modified_at','effective_from','effective_to','valid_until'):
            if clean[key]:
                timestamp(clean[key])
        if clean['effective_from'] and clean['effective_to'] and timestamp(clean['effective_from']) >= timestamp(clean['effective_to']):
            raise AppError('종료일은 적용 시작일 이후여야 합니다.')
        clean.update(source_type=kind, retrieved_at=now(), verification_status='unreviewed', content_hash=hashlib.sha256(body.encode()).hexdigest())
        did = identifier()
        with self.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?)', (did,title,body,json.dumps(clean,ensure_ascii=False)))
        return {'id': did}

    def documents(self):
        return [{**d, 'metadata': json.loads(d['metadata'])} for d in self.rows('SELECT * FROM documents ORDER BY rowid DESC')]

    def document_counts(self):
        rows = self.rows('SELECT metadata FROM documents')
        return len(rows), sum(json.loads(r['metadata']).get('verification_status') == 'reviewed' for r in rows)

    def review(self, data):
        docs = self.rows('SELECT id, metadata FROM documents WHERE id=?', (data.get('id'),))
        if not docs:
            raise AppError('문서를 찾을 수 없습니다.', 404)
        doc = docs[0]
        m = json.loads(doc['metadata'])
        if data.get('approve'):
            if not all(m.get(k) for k in ('effective_from','version','valid_until','topic')) or m['region'] != 'KR' or m['server_type'] != 'live':
                raise AppError('승인하려면 적용일·버전·재검토 기한·주제와 KR/live 범위를 입력해야 합니다.')
            if timestamp(m['valid_until']) <= datetime.now(KST):
                raise AppError('재검토 기한이 지났습니다.')
            m['verification_status'] = 'reviewed'
            m['reviewed_at'] = now()
        else:
            m['verification_status'] = 'unreviewed'
        with self.db() as db:
            db.execute('UPDATE documents SET metadata=? WHERE id=?', (json.dumps(m,ensure_ascii=False),doc['id']))
        return {'ok': True}

    def search(self, query):
        tokens = [t for t in re.findall(r'[가-힣A-Za-z0-9]+', normalize(query).lower()) if len(t)>1]
        if not tokens:
            return [], False
        ranked = []
        current = datetime.now(KST)
        # 본문보다 작은 메타데이터를 먼저 확인한다. 승인 취소와 본문 조회 사이에
        # 다른 쓰기가 끼어들지 않도록 두 조회는 같은 읽기 트랜잭션에서 한다.
        with self.db() as db:
            db.execute('BEGIN')
            eligible = []
            for row in db.execute('SELECT id, title, metadata FROM documents ORDER BY rowid DESC'):
                d = dict(row)
                m = d['metadata'] = json.loads(d['metadata'])
                if (m.get('verification_status') != 'reviewed' or m.get('region') != 'KR'
                        or m.get('server_type') != 'live' or not m.get('effective_from') or not m.get('valid_until')):
                    continue
                if (timestamp(m['effective_from']) > current or timestamp(m['valid_until']) <= current
                        or (m.get('effective_to') and timestamp(m['effective_to']) <= current)):
                    continue
                eligible.append(d)
            # 오래된 SQLite의 매개변수 개수 제한도 지킨다. 순위가 같으면 원래 최신순이다.
            for start in range(0, len(eligible), 500):
                batch = eligible[start:start + 500]
                placeholders = ','.join('?' for _ in batch)
                bodies = {r['id']: r['body'] for r in db.execute(
                    f'SELECT id, body FROM documents WHERE id IN ({placeholders})', [d['id'] for d in batch])}
                for d in batch:
                    d['body'] = bodies[d['id']]
        for d in eligible:
            haystack = normalize(d['title'] + ' ' + d['body']).lower()
            words = haystack.split()
            matches = [t for t in tokens if t in haystack or any(w.startswith(t[:max(2,len(t)-2)]) for w in words)]
            if matches:
                d['score'] = sum(3 if t in d['title'].lower() else 1 for t in matches)
                ranked.append(d)
        ranked.sort(key=lambda x: x['score'], reverse=True)
        # Different active versions for the same reviewed topic are explicitly a conflict.
        topics = {}
        for d in ranked:
            topics.setdefault(d['metadata']['topic'],set()).add(d['metadata']['version'])
        conflict = any(len(v)>1 for v in topics.values())
        selected = ranked[:5]
        for d in selected:
            d['passages'] = [p.strip() for p in re.split(r'\n+', d['body']) if p.strip()]
        return selected, conflict

    # ---- 노작값 ----
    # 같은 장비라도 추옵 급과 잠재 등급에 따라 값이 크게 다르므로 함께 저장한다.
    # source는 값의 출처다. 'user'는 사용자가 직접 알려 준 값, 그 밖은 조회기 이름을 적는다.
    def price_save(self, data):
        item = required(data, 'item', 100)
        price = number(data, 'price', 0, 1e15)
        source = str(data.get('source') or 'user')[:40]
        grade = data.get('add_grade')
        if grade not in (None, ''):
            grade = int(number(data, 'add_grade', 0, 10_000))
        else:
            grade = None
        record = {'id': identifier(), 'item': item, 'add_grade': grade,
                  'potential': (str(data.get('potential'))[:20] if data.get('potential') else None),
                  'price': price, 'source': source,
                  'note': (str(data.get('note'))[:300] if data.get('note') else None),
                  'recorded_at': now()}
        with self.db() as db:
            db.execute('INSERT INTO prices VALUES(:id,:item,:add_grade,:potential,:price,:source,:note,:recorded_at)', record)
        return record

    def price_lookup(self, item, add_grade=None):
        """가장 최근 값을 돌려준다. 급이 주어지면 급이 같은 기록을 먼저 본다."""
        if add_grade is not None:
            rows = self.rows('SELECT * FROM prices WHERE item=? AND add_grade=? ORDER BY recorded_at DESC, rowid DESC LIMIT 1',
                             (item, int(add_grade)))
            if rows:
                return rows[0]
        rows = self.rows('SELECT * FROM prices WHERE item=? ORDER BY recorded_at DESC, rowid DESC LIMIT 1', (item,))
        return rows[0] if rows else None

    def prices(self, limit=500):
        return self.rows('SELECT * FROM prices ORDER BY recorded_at DESC, rowid DESC LIMIT ?', (int(limit),))

    def price_fetch_count(self, day):
        return self.rows('SELECT COUNT(*) AS n FROM prices WHERE recorded_at>=? AND recorded_at<? AND source!=?',
                         (day, (timestamp(day) + timedelta(days=1)).date().isoformat(), 'user'))[0]['n']

    def price_count(self):
        return self.rows('SELECT COUNT(*) AS n FROM prices')[0]['n']

    def session(self, sid=None, title='새 대화', topic=None):
        if sid:
            if not self.rows('SELECT id FROM sessions WHERE id=?', (sid,)):
                raise AppError('대화를 찾을 수 없습니다.', 404)
            return sid
        sid = identifier()
        with self.db() as db:
            db.execute('INSERT INTO sessions(id,title,created_at,topic) VALUES(?,?,?,?)',
                       (sid, title[:50], now(), json.dumps(topic, ensure_ascii=False) if topic else None))
        return sid

    def session_topic(self, sid):
        rows = self.rows('SELECT topic FROM sessions WHERE id=?', (sid,))
        try:
            return json.loads(rows[0]['topic']) if rows and rows[0]['topic'] else None
        except ValueError:
            return None

    def sessions(self):
        return [{**r, 'topic': json.loads(r['topic']) if r.get('topic') else None}
                for r in self.rows('SELECT * FROM sessions ORDER BY rowid DESC')]

    def messages(self, sid):
        return [{**r,'payload':json.loads(r['payload'])} for r in self.rows('SELECT * FROM messages WHERE session_id=? ORDER BY rowid', (sid,))]

    def message(self, sid, role, payload):
        with self.db() as db:
            db.execute('INSERT INTO messages VALUES(?,?,?,?,?)', (identifier(),sid,role,json.dumps(payload,ensure_ascii=False),now()))
