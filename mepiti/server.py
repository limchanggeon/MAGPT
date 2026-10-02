import argparse
import copy
from concurrent.futures import Future
from contextlib import nullcontext
import json
import mimetypes
import os
import platform
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .adapters import (CLOUD_MODEL, CLOUD_PROVIDERS, Claude, FixedKey, Gemini, ModelRouter, Nexon, Ollama, OpenAI, Vault,
                       install_ollama_mac, key_problem, recognize, system_info)
from . import models
from .chat import answer
from .core import AppError, Store, identifier, now, required
from . import backup, desktop, earnings, goals, history, notices, peers, prices, starforce, statcalc
from .updater import Updater

STATIC = Path(__file__).parent/'static'
IMAGE_CACHE_SIZE = 96
IMAGE_CACHE_BYTES = 12 * 1024 * 1024
_IMAGE_INIT_LOCK = threading.Lock()


class ImageCache:
    """정적 이미지의 동시 요청을 합치고 개수와 실제 본문 크기를 함께 제한한다."""
    def __init__(self):
        from collections import OrderedDict
        self.entries = OrderedDict()
        self.inflight = {}
        self.size = 0
        self.lock = threading.Lock()

    def get(self, url, fetch):
        with self.lock:
            if url in self.entries:
                self.entries.move_to_end(url)
                return self.entries[url]
            pending = self.inflight.get(url)
            owner = pending is None
            if owner:
                pending = Future()
                self.inflight[url] = pending
        if not owner:
            return pending.result()
        try:
            value = fetch()
            with self.lock:
                self.entries[url] = value
                self.size += len(value[0])
                while len(self.entries) > IMAGE_CACHE_SIZE or self.size > IMAGE_CACHE_BYTES:
                    _, removed = self.entries.popitem(last=False)
                    self.size -= len(removed[0])
            pending.set_result(value)
            return value
        except BaseException as e:
            pending.set_exception(e)
            raise
        finally:
            with self.lock:
                self.inflight.pop(url, None)


def nexon_image(app, url):
    """허용된 넥슨 정적 이미지만 받아 캐시한다. API 키는 사용하지 않는다."""
    from urllib.request import Request, urlopen
    try:
        parsed = urlparse(url or '')
        allowed = (parsed.scheme == 'https' and parsed.hostname == 'open.api.nexon.com'
                   and not parsed.port and not parsed.username
                   and parsed.path.startswith('/static/maplestory/') and len(url) <= 4096)
    except (ValueError, TypeError):
        allowed = False
    if not allowed:
        raise AppError('넥슨 이미지 주소가 아닙니다.', 400)
    with _IMAGE_INIT_LOCK:
        if not hasattr(app, 'image_cache'):
            app.image_cache = ImageCache()
        cache = app.image_cache

    def fetch():
        try:
            with urlopen(Request(url, headers={'User-Agent': 'Mepiti'}), timeout=10) as response:
                kind = (response.headers.get('Content-Type') or '').split(';')[0]
                body = response.read(3_000_001)
        except Exception:
            raise AppError('넥슨 이미지를 받지 못했습니다.', 502)
        if not kind.startswith('image/') or len(body) > 3_000_000:
            raise AppError('넥슨 이미지가 아닙니다.', 502)
        return body, kind

    return cache.get(url, fetch)

class CachedNexon:
    """같은 캐릭터를 짧은 간격으로 다시 묻는 경우 넥슨 API를 다시 부르지 않는다.

    캐릭터 질문 한 번에 id/basic/stat/item-equipment/android까지 여러 번 호출하므로,
    캐시가 없으면 대화를 몇 번만 이어가도 요청 한도(OPENAPI00007)에 걸린다.
    """
    TTL = 180
    MAX_ENTRIES = 64

    def __init__(self, nexon):
        self.nexon = nexon
        self.cache = {}
        self.lock = threading.Lock()
        self.inflight = {}
        self.generation = 0

    def get(self, path, query):
        return self.nexon.get(path, query)

    def character(self, name, details=False):
        return self._cached(('character', name, bool(details)), lambda: self.nexon.character(name, details=details))

    def clear(self):
        with self.lock:
            self.generation += 1
            self.cache.clear()

    def prime(self, key, data, generation=None):
        with self.lock:
            if generation is None or generation == self.generation:
                self._put(key, data)

    def _put(self, key, data):
        self.cache[key] = (time.monotonic(), copy.deepcopy(data))
        while len(self.cache) > self.MAX_ENTRIES:
            oldest = min(self.cache, key=lambda k: self.cache[k][0])
            self.cache.pop(oldest)

    def characters(self):
        return self._cached(('list',), self.nexon.characters)

    def union(self, name):
        return self._cached(('union', name), lambda: self.nexon.union(name))

    def scheduler(self, name, day=None):
        return self.nexon.scheduler(name, day)

    def basic_on(self, name, day=None):
        return self.nexon.basic_on(name, day)

    def starforce_history(self, day):
        return self.nexon.starforce_history(day)

    def notices(self, kind):
        return self._cached(('notices', kind), lambda: self.nexon.notices(kind))

    def notice_detail(self, kind, notice_id):
        return self.nexon.notice_detail(kind, notice_id)

    def _cached(self, key, fetch):
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < self.TTL:
                return copy.deepcopy(hit[1])
            pending_key = (self.generation, key)
            pending = self.inflight.get(pending_key)
            owner = pending is None
            if owner:
                pending = Future()
                self.inflight[pending_key] = pending
        if not owner:
            return copy.deepcopy(pending.result())
        try:
            data = fetch()
            with self.lock:
                # 키가 바뀐 사이 끝난 이전 요청은 새 캐시에 넣지 않는다.
                if pending_key[0] == self.generation:
                    self._put(key, data)
            pending.set_result(copy.deepcopy(data))
            return data
        except BaseException as e:
            pending.set_exception(e)
            raise
        finally:
            with self.lock:
                self.inflight.pop(pending_key, None)


class Application:
    def __init__(self, folder):
        # 새 버전으로 처음 켜면 표를 고치기 전에 데이터를 백업한다(mepiti/backup.py). 데이터는 설치 폴더 밖이라 업데이트로 지워지지 않는다.
        self.startup_backup = backup.on_start(folder, __version__)
        self.store = Store(folder)
        self.store.set_setting(backup.VERSION_SETTING, __version__)
        self.vault = Vault()
        self.nexon = CachedNexon(Nexon(self.vault))
        self.cloud_vault = Vault('gemini-api-key', 'Gemini API 키')
        self.gemini = Gemini(self.cloud_vault, lambda: self.store.setting('gemini_model') or None)
        # 유료 클라우드. 키는 회사마다 따로, 모델은 설정값 claude_model·openai_model(없으면 첫 항목).
        self.claude = Claude(Vault('anthropic-api-key', 'Claude API 키'), lambda: self.store.setting('claude_model') or None)
        self.openai = OpenAI(Vault('openai-api-key', 'OpenAI API 키'), lambda: self.store.setting('openai_model') or None)
        self.model = ModelRouter(Ollama(), lambda: self.clouds)   # 고른 모델에 따라 로컬·클라우드로 보낸다
        self.auction = None                               # 앱 창으로 실행하면 desktop.run이 붙인다(mepiti/auction.py)
        self.updater = Updater(folder)                     # 새 버전 확인·업데이트(mepiti/updater.py)
        self.peers = peers.Peers(self.store, lambda: self.nexon)   # 비슷한 유저 장비 통계(천천히 모은다, mepiti/peers.py)
        self.peers.start()                                 # 어제 모으다 만 후보가 있으면 이어서(오늘 몫 안에서)
        self.quit_app = None                              # make_server가 붙인다. 업데이트 때 메피티를 끈다
        self.token = secrets.token_urlsafe(32)
        self.download = {'running':False}
        self.ollama_setup = {'running':False}
        self.lock = threading.Lock()
        self.chat_lock = threading.Lock()

    def status(self):
        try:
            key = bool(self.vault.get())
            vault_error = None
        except AppError as e:
            key, vault_error = False, str(e)
        document_count, reviewed_count = self.store.document_counts()
        ollama = self.model.status()
        system = system_info(self.store.folder)
        selected = self.store.setting('model')
        clouds = {name: self.cloud_status(name) for name in CLOUD_PROVIDERS}
        cloud = clouds[CLOUD_MODEL]
        # 답변을 쓸 준비가 됐는가. 클라우드는 그 회사 키가 있으면, 로컬은 Ollama에 그 모델이 있으면.
        ready = clouds[selected]['key_present'] if selected in clouds else bool(selected) and selected in ollama['models']
        return {'version':__version__,'model':ollama,'selected_model':selected,'ready':ready,'cloud':cloud,'clouds':clouds,'key_present':key,'vault_error':vault_error,'system':system,'documents':document_count,'reviewed_documents':reviewed_count,'download':dict(self.download),'storage_path':str(self.store.folder),
                'presets':models.describe(system,ollama['models'],selected,{n:c['key_present'] for n,c in clouds.items()}),
                'setup_choice':models.setup_choice(self.store.folder) if not selected else None,
                'ollama_setup':dict(self.ollama_setup),
                'tour_done':self.store.setting('tour_done')=='1'}

    def nexon_for(self, key):
        """저장하지 않은 키로 넥슨을 부르는 클라이언트. 데모·테스트는 이 메서드를 바꿔 끼운다."""
        return Nexon(FixedKey(key))

    def connect_key(self, key):
        """새 키를 넥슨에 먼저 시험하고 저장한다. 넥슨이 거절한 키는 저장하지 않아, 이미 쓰던 키가 그대로 남는다."""
        key = key.strip()
        if not 10 <= len(key) <= 500 or any(c.isspace() for c in key):
            raise AppError('넥슨 API 키 형식을 확인해 주세요. 복사한 키를 그대로 붙여 넣으면 됩니다.')
        try:
            listing = self.nexon_for(key).characters()
        except AppError as e:
            if key_problem(e) == 'invalid':
                raise AppError('넥슨이 이 키를 받아 주지 않았어요. 복사할 때 빠진 글자가 없는지, 메이플스토리용 키인지 확인해 주세요. '
                               '(이미 저장된 키가 있었다면 그대로 둡니다.) ' + str(e), 400)
            # 인터넷·요청 한도·점검 때문이면 키가 맞을 수도 있다. 저장하고 나중에 다시 확인한다.
            self.vault.save(key)
            self.forget_nexon_cache()
            return {'state':'unverified','message':str(e)}
        self.vault.save(key)
        self.forget_nexon_cache()
        self._prime_listing(listing)
        return {'state':'ok','characters':len(listing['characters'])}

    def check_key(self):
        """저장된 키가 지금도 쓸 수 있는지. 앱을 켤 때 한 번 부른다."""
        generation = self.nexon.generation if isinstance(self.nexon, CachedNexon) else None
        try:
            key = self.vault.get()
        except AppError as e:
            return {'state':'vault_error','message':str(e)}
        if not key:
            return {'state':'missing'}
        try:
            listing = self.nexon_for(key).characters()
        except AppError as e:
            return {'state':key_problem(e),'message':str(e)}
        self._prime_listing(listing, generation)
        return {'state':'ok'}

    def _prime_listing(self, listing, generation=None):
        if isinstance(self.nexon, CachedNexon):
            self.nexon.prime(('list',), listing, generation)

    def data_status(self):
        """설정의 '데이터 보관': 데이터 위치, OS 보안 저장소에 있는 키, 백업 목록. 키 값은 돌려주지 않는다."""
        def present(vault):
            try:
                return bool(vault.get())
            except AppError:
                return None                                  # 보안 저장소를 못 읽음
        path = backup.data_file(self.store.folder)
        store_name = {'Darwin': 'macOS 키체인', 'Windows': 'Windows 자격 증명 관리자'}.get(platform.system(), 'OS 보안 저장소')
        return {'folder': str(self.store.folder), 'data_file': str(path),
                'size_kb': round(path.stat().st_size / 1024, 1) if path.exists() else 0,
                'key_store': store_name,
                'keys': {'넥슨': present(self.vault), 'Gemini': present(self.gemini.vault),
                         'Claude': present(self.claude.vault), 'ChatGPT': present(self.openai.vault)},
                'backups': backup.listing(self.store.folder), 'keep': backup.KEEP,
                'startup_backup': self.startup_backup.name if self.startup_backup else None}

    def peer_report(self, name=None, collect=False, simulate=False, cp=None):
        """비슷한 유저 통계와 내 장비 비교. collect면 후보를 (다시) 고르고 모으기를 시작한다.
        simulate면 내 스탯 출처(넥슨 약 22회, 하루 한 번)를 받아 교체 시뮬레이션까지 한다. 아니면 받아 둔 것이 있을 때만."""
        name = name or self.main_character_name()
        if not name:
            raise AppError('캐릭터 화면에서 대표 캐릭터를 먼저 등록해 주세요.')
        target = self.store.setting(peers.TARGET) or {}
        profile = None
        try:
            profile = self.nexon.character(name, details=True)
        except AppError:
            pass
        if collect:
            goal = peers.parse_cp(cp) if cp else target.get('cp')
            if not goal:
                raise AppError("목표 전투력을 '2억5천'처럼 적어 주세요.")
            self.peers.choose(name, goal)
        self.peers.start()
        ledger = None
        try:
            ledger = statcalc.load(self.store, self.nexon.get, name, fetch_missing=simulate)
        except AppError:
            if simulate:
                raise
        return {**peers.compare(self.store, profile, self.peers.status(), ledger), 'name': name}

    def main_character_name(self):
        chars = self.store.characters(include_snapshots=False)
        main = next((c for c in chars if c['main']), None) or (chars[0] if chars else None)
        return main['name'] if main else None

    def auction_route(self, path):
        if self.auction is None:
            return {'available': False,
                    'reason': '경매장 연결은 메피티를 앱 창으로 실행했을 때만 쓸 수 있습니다(브라우저로 연 경우 제외).'}
        if path == '/api/auction/open':
            return self.auction.open()
        if path == '/api/auction/check':
            state = self.auction.check()
            if state['logged_in'] and not state['error'] and state['character']:
                self.store.set_setting(prices.FETCH_SETTING, '1')   # 연결을 확인하면 노작값 자동 조회를 켠다
            return state
        return self.auction.status()

    @property
    def clouds(self):
        """클라우드 제공자. 테스트·데모가 self.gemini 등을 바꿔 끼워도 따라가도록 매번 모은다."""
        return {CLOUD_MODEL: self.gemini, 'claude': self.claude, 'openai': self.openai}

    def cloud_status(self, provider=CLOUD_MODEL):
        engine = self.clouds[provider]
        try:
            present, error = bool(engine.vault.get()), None
        except AppError as e:
            present, error = False, str(e)
        choices = [{'id': m, 'label': label} for m, label in getattr(engine, 'MODELS', ())]
        if provider == CLOUD_MODEL:
            # model: 실제로 쓰는 이름(한도에 막히면 다른 모델일 수 있다), chosen: 사용자가 고른 것
            model = engine.model or self.store.setting('cloud_model') or None
            chosen = getattr(engine, 'chosen', None)
        else:
            model = chosen = engine.model
        return {'key_present':present,'vault_error':error,'model':model,'chosen':chosen,'choices':choices}

    def connect_cloud_key(self, key, use=False, provider=CLOUD_MODEL):
        """클라우드 키를 먼저 시험하고 저장한다. 회사가 거절한 키는 저장하지 않는다. use면 사용 모델도 그 클라우드로 정한다."""
        if provider not in self.clouds:
            raise AppError('알 수 없는 클라우드입니다.')
        engine = self.clouds[provider]
        key = key.strip()
        if not 20 <= len(key) <= 300 or any(c.isspace() for c in key):
            raise AppError('API 키 형식을 확인해 주세요. 복사한 키를 그대로 붙여 넣으면 됩니다.')
        state = {'state':'ok'}
        try:
            state['model'] = engine.check(key)
        except AppError as e:
            if getattr(e, 'kind', 'unverified') in ('invalid', 'region', 'model'):
                raise AppError(str(e), 400)
            # 인터넷·일시 오류·크레딧 부족이면 키는 맞을 수 있다. 저장하고 쓸 때 다시 확인한다.
            state = {'state':'unverified','message':str(e)}
        engine.vault.save(key)
        if provider == CLOUD_MODEL and state.get('model'):
            self.store.set_setting('cloud_model', state['model'])
        if use:
            self.store.set_setting('model', provider)
            models.clear_setup(self.store.folder)
        return state

    def choose_cloud_model(self, provider, model):
        """Claude·ChatGPT 중 쓸 모델(예: Opus 5·Sonnet 5). 목록에 있는 것만."""
        engine = self.clouds.get(provider)
        if engine is None or model not in dict(getattr(engine, 'MODELS', ())):
            raise AppError('고를 수 있는 모델이 아닙니다.')
        self.store.set_setting(f'{provider}_model', model)
        if provider == CLOUD_MODEL:
            engine.model = None                    # 다음 질문 때 고른 모델로 다시 확인한다
        return self.cloud_status(provider)

    def select_model(self, provider, model=None):
        """화면 위쪽 모델 선택: 클라우드(회사+모델) 또는 로컬(Ollama 모델 이름)로 바로 바꾼다.
        그 회사 키가 없으면 need_key를 돌려주고 화면이 키 입력을 연다."""
        if provider in self.clouds:
            if model:
                self.choose_cloud_model(provider, model)
            if not self.cloud_status(provider)['key_present']:
                return {'need_key':True,'provider':provider}
            self.store.set_setting('model', provider)
            models.clear_setup(self.store.folder)
            return {'selected':provider}
        if provider == 'local':
            if not model or model not in self.model.status()['models']:
                raise AppError('설치된 로컬 모델을 골라 주세요.')
            self.store.set_setting('model', model)
            return {'selected':model}
        raise AppError('고를 수 있는 모델이 아닙니다.')

    def forget_nexon_cache(self):
        if isinstance(self.nexon, CachedNexon):
            self.nexon.clear()
            self.nexon.nexon.__dict__.pop('_ocids', None)
        self.store.set_setting(earnings.ACCOUNT_CHARACTERS, [])
        self.store.set_setting(goals.EXP_DAYS, {})
        self.store.set_setting(history.FETCHED, [])

    def start_pull(self, model, select_after=False):
        """모델 다운로드를 뒤에서 돌린다. select_after면 끝난 뒤 그 모델을 사용 모델로 정한다."""
        with self.lock:
            if self.download.get('running'): raise AppError('이미 모델을 다운로드 중입니다.',409)
            self.download = {'running':True,'model':model,'status':'다운로드 준비 중'}
        def run():
            try:
                self.model.pull(model,lambda event:self.download.update(event))
                self.download.update(status='다운로드 완료')
                if select_after:
                    self.store.set_setting('model',model)
                    models.clear_setup(self.store.folder)
                    self.download.update(status='다운로드 완료 · 사용 모델로 정했습니다')
            except AppError as e:
                self.download.update(status=str(e),error=True)
            finally:
                self.download['running'] = False
        threading.Thread(target=run,daemon=True).start()
        return dict(self.download)

    def route(self,method,path,query,data):
        s = self.store
        if method == 'GET':
            if path == '/api/status': return self.status()
            if path == '/api/sessions': return s.sessions()
            if path == '/api/messages': return s.messages(query.get('session_id',[''])[0])
            if path == '/api/characters': return s.characters()
            if path == '/api/download': return dict(self.download)
            if path == '/api/history/starforce': return history.overview(s)
            if path == '/api/notices': return {'events': notices.active_events(s), 'updates': s.setting(notices.UPDATES) or [],
                                               'alert': s.setting(notices.ALERT) or None, 'synced_at': s.setting(notices.SYNCED) or None}
            if path == '/api/earnings': return earnings.overview(s, query.get('week',[None])[0], query.get('month',[None])[0])
            if path == '/api/prices': return {'prices':s.prices(),**prices.status(s)}
            if path == '/api/auction/status': return self.auction_route(path)
            if path == '/api/data': return self.data_status()
            if path == '/api/goals': return goals.meso_plan(s)
            if path == '/api/peers': return self.peer_report(query.get('name',[None])[0])
            if path == '/api/update': return self.updater.public()
        if method == 'POST':
            if path == '/api/chat':
                if not self.chat_lock.acquire(blocking=False):
                    raise AppError('이전 질문을 처리 중입니다. 잠시 후 다시 시도해 주세요.',409)
                try:
                    return answer(s,self.model,data,self.nexon,self.peers)
                finally:
                    self.chat_lock.release()
            if path == '/api/characters/profile': return self.nexon.character(required(data,'name',30), details=True)
            if path == '/api/characters/discover':
                cache = self.nexon if isinstance(self.nexon, CachedNexon) else None
                generation = cache.generation if cache else None
                found = self.nexon.characters()
                # 수익 기록의 캐릭터 고르기에 쓴다. 이 컴퓨터의 데이터 파일에만 둔다.
                with (cache.lock if cache else nullcontext()):
                    if cache and generation != cache.generation:
                        raise AppError('조회 중 API 키가 바뀌었습니다. 캐릭터 목록을 다시 불러와 주세요.', 409)
                    s.set_setting(earnings.ACCOUNT_CHARACTERS, [{'name': c.get('name'), 'world': c.get('world'), 'level': c.get('level')}
                                                              for c in found.get('characters') or [] if c.get('name')][:200])
                return found
            if path == '/api/characters': return s.character_save(data)
            if path == '/api/characters/refresh':
                chars = s.rows('SELECT * FROM characters WHERE id=?',(required(data,'id',100),))
                if not chars: raise AppError('캐릭터를 찾을 수 없습니다.',404)
                snapshot = self.nexon.character(chars[0]['name'])
                with s.db() as db:
                    db.execute('INSERT INTO snapshots VALUES(?,?,?,?)',(identifier(),chars[0]['id'],json.dumps(snapshot,ensure_ascii=False),now()))
                return snapshot
            if path == '/api/characters/delete':
                with s.db() as db:
                    db.execute('DELETE FROM characters WHERE id=?',(required(data,'id',100),))
                    if not db.execute('SELECT 1 FROM characters WHERE main=1').fetchone():
                        db.execute('UPDATE characters SET main=1 WHERE id=(SELECT id FROM characters ORDER BY created_at LIMIT 1)')
                return {'ok':True}
            if path == '/api/sessions/delete':
                with s.db() as db:
                    db.execute('DELETE FROM sessions WHERE id=?',(required(data,'id',100),))
                return {'ok':True}
            if path == '/api/earnings': return earnings.add(s, data)
            if path == '/api/earnings/delete': return earnings.delete(s, required(data,'id',100))
            if path == '/api/goals/meso': return goals.meso_plan(s, data.get('target'), data.get('current'))
            if path == '/api/peers/collect': return self.peer_report(data.get('name') or None, collect=True, cp=data.get('cp'))
            if path == '/api/peers/simulate': return self.peer_report(data.get('name') or None, simulate=True)
            if path == '/api/goals/exp': return goals.exp_plan(s, self.nexon, required(data,'name',30))
            if path == '/api/earnings/capture':
                return earnings.read_capture(self.model, s.setting('model'), required(data, 'image', 8_100_000))
            if path == '/api/earnings/piece-price': return earnings.piece_price(s, self.auction, bool(data.get('refresh')))
            if path == '/api/earnings/scheduler':
                names = [c['name'] for c in s.characters(include_snapshots=False)]
                if not names: raise AppError('캐릭터 화면에서 관리할 캐릭터를 먼저 등록하세요.')
                return earnings.scheduled_bosses(s, self.nexon, names[:20])
            if path == '/api/starforce': return starforce.expected(data)
            if path == '/api/prices': return s.price_save(data)
            if path == '/api/prices/delete':
                with s.db() as db:
                    db.execute('DELETE FROM prices WHERE id=?',(required(data,'id',100),))
                return {'ok':True}
            if path == '/api/prices/fetch':
                s.set_setting(prices.FETCH_SETTING,'1' if data.get('enabled') else '0')
                return prices.status(s)
            if path == '/api/ocr': return recognize(required(data,'image',8_100_000))
            if path == '/api/notices/sync': return notices.sync(s, self.nexon, force=True)
            if path == '/api/history/starforce/fetch': return history.fetch(s, self.nexon, data.get('days') or 14)
            if path == '/api/history/starforce/level': return history.set_level(s, data.get('item'), data.get('level'))
            if path == '/api/settings/key':
                self.vault.save(required(data,'key',500))
                self.forget_nexon_cache()
                return {'ok':True}
            if path == '/api/settings/key/connect':
                return self.connect_key(required(data,'key',500))
            if path == '/api/settings/key/check':
                return self.check_key()
            if path == '/api/settings/key/delete':
                self.vault.delete()
                self.forget_nexon_cache()
                return {'ok':True}
            if path in ('/api/auction/open', '/api/auction/check'):
                return self.auction_route(path)
            if path == '/api/cloud/key/connect':
                return self.connect_cloud_key(required(data,'key',300), bool(data.get('use')), data.get('provider') or CLOUD_MODEL)
            if path == '/api/cloud/key/delete':
                provider = data.get('provider') or CLOUD_MODEL
                if provider not in self.clouds: raise AppError('알 수 없는 클라우드입니다.')
                self.clouds[provider].vault.delete()
                if provider == CLOUD_MODEL: self.gemini.model = None
                return {'ok':True}
            if path == '/api/model/select':
                return self.select_model(required(data,'provider',20), data.get('model'))
            if path == '/api/cloud/model':
                return self.choose_cloud_model(required(data,'provider',20), required(data,'model',60))
            if path == '/api/update/check':
                self.updater.check()
                return self.updater.public()
            if path == '/api/update/apply':
                if not self.quit_app: raise AppError('지금은 업데이트를 할 수 없어요.', 409)
                return self.updater.start(self.quit_app)
            if path == '/api/data/backup':
                made = backup.make(s.folder, 'manual')
                return {'made': made.name, **self.data_status()}
            if path == '/api/tour':
                # 사용법 안내를 끝냈는지. 앱 창(pywebview)은 브라우저 저장소가 남지 않을 수 있어 DB에 둔다.
                s.set_setting('tour_done','1' if data.get('done') else '0')
                return {'ok':True}
            if path == '/api/settings/model':
                name = required(data,'model',100)
                if name not in self.model.status()['models'] or 'cloud' in name.lower():
                    raise AppError('설치된 로컬 모델을 선택해 주세요.')
                s.set_setting('model',name)
                return {'ok':True}
            if path == '/api/model/pull':
                return self.start_pull(required(data,'model',100))
            if path == '/api/model/preset':
                # 2B·8B 중 하나를 고른다. 받아 둔 모델이면 바로 쓰고, 없으면 받은 뒤 쓴다.
                preset = models.by_id(required(data,'id',20))
                if not preset: raise AppError('고를 수 있는 모델이 아닙니다.')
                if preset.get('cloud'):
                    # 클라우드는 받을 것이 없다. 키가 없으면 화면이 그 회사 키 입력을 먼저 보여 준다.
                    if not self.cloud_status(preset['model'])['key_present']:
                        return {'need_key':True,'provider':preset['model']}
                    s.set_setting('model',preset['model'])
                    models.clear_setup(s.folder)
                    return {'selected':preset['model']}
                if preset['model'] in self.model.status()['models']:
                    s.set_setting('model',preset['model'])
                    models.clear_setup(s.folder)
                    return {'selected':preset['model']}
                return self.start_pull(preset['model'], select_after=True)
            if path == '/api/model/setup/skip':
                models.clear_setup(s.folder)
                return {'ok':True}
            if path == '/api/ollama/install':
                with self.lock:
                    if self.ollama_setup.get('running'): raise AppError('이미 Ollama를 설치하고 있습니다.',409)
                    self.ollama_setup = {'running':True,'status':'준비 중'}
                def install():
                    try:
                        install_ollama_mac(lambda event:self.ollama_setup.update(event))
                    except AppError as e:
                        self.ollama_setup.update(status=str(e),error=True)
                    finally:
                        self.ollama_setup['running'] = False
                threading.Thread(target=install,daemon=True).start()
                return dict(self.ollama_setup)
        raise AppError('요청 경로를 찾을 수 없습니다.',404)


class Server(ThreadingHTTPServer):
    """이미 떠 있는 메피티와 같은 포트를 잡지 못하게 한다(두 번째 실행은 떠 있는 창을 앞으로 가져온다).
    Windows의 SO_REUSEADDR은 다른 프로세스가 쓰는 포트도 잡게 해 줘서, 두 번째 실행이 기존 메피티를 못 알아보고 그대로 떠 있었다(CI에서 멈춤)."""
    allow_reuse_address = sys.platform != 'win32'

    def server_bind(self):
        if sys.platform == 'win32':
            import socket
            self.socket.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_EXCLUSIVEADDRUSE', -5), 1)
        super().server_bind()


def make_server(app,port=8765):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'Mepiti'
        def log_message(self,*args):
            pass  # Requests, keys and questions are not written to access logs.

        def respond(self,status,body,content_type='application/json; charset=utf-8'):
            if not isinstance(body,bytes): body = json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data: https://open.api.nexon.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            try: self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError): pass

        def handle_request(self):
            try:
                host = self.headers.get('Host','')
                allowed = {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
                if host not in allowed: raise AppError('허용되지 않은 호스트입니다.',403)
                origin = self.headers.get('Origin')
                if origin and origin not in {'http://'+h for h in allowed}: raise AppError('다른 사이트의 요청은 허용되지 않습니다.',403)
                parsed = urlparse(self.path)
                path = parsed.path
                if self.command == 'GET' and path == '/api/bootstrap':
                    if self.headers.get('Sec-Fetch-Site') == 'cross-site': raise AppError('접근 거부',403)
                    return self.respond(200,{'token':app.token})
                if path.startswith('/api/'):
                    if not secrets.compare_digest(self.headers.get('X-Mepiti-Token',''),app.token): raise AppError('페이지를 새로고침해 주세요.',403)
                    data = {}
                    if self.command == 'POST':
                        if self.headers.get('Content-Type','').split(';')[0] != 'application/json': raise AppError('JSON 요청만 허용됩니다.',415)
                        try: size = int(self.headers.get('Content-Length','0'))
                        except ValueError: raise AppError('잘못된 요청 길이입니다.')
                        if not 0 < size <= 8_200_000: raise AppError('요청 용량을 확인해 주세요.',413)
                        try:
                            data = json.loads(self.rfile.read(size))
                            if not isinstance(data,dict): raise ValueError()
                        except (ValueError,UnicodeError): raise AppError('올바른 JSON 객체가 필요합니다.')
                    if self.command == 'POST' and path == '/api/window/focus':
                        # 다시 실행했을 때 새 창 대신 떠 있는 창을 앞으로 가져온다(앱 창 모드일 때만).
                        focus = getattr(app,'on_focus',None)
                        if focus:
                            threading.Thread(target=focus,daemon=True).start()
                        return self.respond(200,{'focused':bool(focus)})
                    if self.command == 'POST' and path == '/api/shutdown':
                        self.respond(200,{'ok':True})
                        app.quit_app()
                        return
                    result = app.route(self.command,path,parse_qs(parsed.query),data)
                    return self.respond(200,result)
                if self.command != 'GET': raise AppError('요청 경로를 찾을 수 없습니다.',404)
                if path == '/nexon-image':
                    # 넥슨 캐릭터·장비 이미지를 앱이 대신 받아 준다. 창(WebView2 등)에서 넥슨 이미지가 막히거나,
                    # 외형 여백 자르기(캔버스)가 교차 출처로 막힐 때 쓴다. 넥슨 정적 이미지 주소만 받는다.
                    if self.headers.get('Sec-Fetch-Site') == 'cross-site': raise AppError('접근 거부',403)
                    body, kind = nexon_image(app, parse_qs(parsed.query).get('u',[''])[0])
                    return self.respond(200, body, kind)
                filename = {'/':'index.html','/app.js':'app.js','/characters.js':'characters.js','/tour.js':'tour.js','/style.css':'style.css','/favicon.svg':'favicon.svg',
                            '/icon.png':'icon.png','/sol-erda-piece.png':'sol-erda-piece.png'}.get(path)
                if not filename: raise AppError('파일을 찾을 수 없습니다.',404)
                content = (STATIC/filename).read_bytes()
                content_type = mimetypes.guess_type(filename)[0] or 'text/plain'
                if content_type.startswith('text/') or filename.endswith(('.js', '.svg')):
                    content_type += '; charset=utf-8'
                self.respond(200,content,content_type)
            except AppError as e:
                self.respond(e.status,{'error':str(e)})
            except Exception:
                self.respond(500,{'error':'처리 중 오류가 발생했습니다. 입력값과 앱 상태를 확인해 주세요.'})
        do_GET = handle_request
        do_POST = handle_request
    server = Server(('127.0.0.1',port),Handler)
    server.daemon_threads = True

    def quit_app():
        # 서버를 끄고, 앱 창으로 실행 중이면 창도 닫는다(설정의 '앱 종료'·업데이트가 쓴다).
        threading.Thread(target=server.shutdown,daemon=True).start()
        close = getattr(app,'on_shutdown',None)
        if close:
            threading.Thread(target=close,daemon=True).start()
    app.quit_app = quit_app
    return server


def say(text, stream=None):
    """콘솔 안내 출력. 창 모드(콘솔 없음)나 한글을 못 쓰는 콘솔(Windows cp1252 등)에서도 멈추지 않는다."""
    stream = stream or sys.stdout
    if stream is None:
        return
    try:
        print(text, file=stream, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(stream, 'encoding', None) or 'ascii'
        print(text.encode(encoding, 'replace').decode(encoding), file=stream, flush=True)
    except (OSError, ValueError):
        pass


def window_only(url):
    webview = desktop.load_webview()
    webview.create_window(desktop.TITLE, desktop.window_url(url), width=1440, height=940, min_size=(960, 640),
                          background_color=desktop.BACKGROUND, text_select=True, zoomable=False)
    webview.start()


def focus_existing(port):
    """같은 포트에 떠 있는 메피티 창을 앞으로 가져온다. 앱 창이 아니거나 메피티가 아니면 False."""
    from urllib.request import Request, urlopen
    base = f'http://127.0.0.1:{port}'
    try:
        with urlopen(base + '/api/bootstrap', timeout=3) as r:
            token = json.loads(r.read())['token']
        request = Request(base + '/api/window/focus', data=b'{}', method='POST',
                          headers={'Content-Type': 'application/json', 'X-Mepiti-Token': token})
        with urlopen(request, timeout=3) as r:
            return bool(json.loads(r.read()).get('focused'))
    except Exception:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description='메피티 로컬 앱')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data-dir',default=os.environ.get('MEPITI_DATA_DIR',str(Path.home()/'.mepiti')))
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--window',action='store_true',help='브라우저 대신 앱 창으로 연다(pywebview 필요)')
    args = parser.parse_args(argv)
    app = Application(args.data_dir)
    try:
        server = make_server(app,args.port)
    except OSError:
        desktop.close_splash()            # 이미 떠 있는 메피티가 있다 — 로딩 창은 필요 없다
        if args.window and focus_existing(args.port):
            # 이미 앱 창이 떠 있다. 보통 프로그램처럼 새 창 대신 그 창을 앞으로 가져온다.
            return 0
        if args.window and desktop.load_webview():
            # 떠 있는 메피티가 브라우저 모드면 그 화면을 새 창으로 연다.
            window_only(f'http://127.0.0.1:{args.port}')
            return 0
        say('포트를 사용 중입니다. 다른 --port 값으로 실행해 주세요.',sys.stderr)
        return 1
    url = f'http://127.0.0.1:{server.server_port}'
    say(f'메피티 {__version__} · {url}\n저장 위치: {args.data_dir}\n종료: Ctrl+C')
    if args.window and desktop.run(server,app,url):
        server.server_close()
        return 0
    desktop.close_splash()                # 창을 못 만들어 브라우저로 연다
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0
