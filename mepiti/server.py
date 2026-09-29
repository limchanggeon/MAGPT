import argparse
import json
import mimetypes
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .adapters import Nexon, Ollama, Vault, install_ollama_mac, recognize, system_info
from . import models
from .chat import answer
from .core import AppError, Store, identifier, now, required
from . import desktop, earnings, history, notices, prices, starforce

STATIC = Path(__file__).parent/'static'

class CachedNexon:
    """같은 캐릭터를 짧은 간격으로 다시 묻는 경우 넥슨 API를 다시 부르지 않는다.

    캐릭터 질문 한 번에 id/basic/stat/item-equipment/android까지 여러 번 호출하므로,
    캐시가 없으면 대화를 몇 번만 이어가도 요청 한도(OPENAPI00007)에 걸린다.
    """
    TTL = 180

    def __init__(self, nexon):
        self.nexon = nexon
        self.cache = {}
        self.lock = threading.Lock()

    def character(self, name, details=False):
        key = (name, bool(details))
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < self.TTL:
                return hit[1]
        data = self.nexon.character(name, details=details)
        with self.lock:
            self.cache[key] = (time.monotonic(), data)
            if len(self.cache) > 40:
                oldest = min(self.cache, key=lambda k: self.cache[k][0])
                self.cache.pop(oldest, None)
        return data

    def characters(self):
        return self._cached(('list',), self.nexon.characters)

    def union(self, name):
        return self._cached(('union', name), lambda: self.nexon.union(name))

    def scheduler(self, name, day=None):
        return self.nexon.scheduler(name, day)

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
                return hit[1]
        data = fetch()
        with self.lock:
            self.cache[key] = (time.monotonic(), data)
        return data


class Application:
    def __init__(self, folder):
        self.store = Store(folder)
        self.vault = Vault()
        self.nexon = CachedNexon(Nexon(self.vault))
        self.model = Ollama()
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
        docs = self.store.documents()
        ollama = self.model.status()
        system = system_info(self.store.folder)
        selected = self.store.setting('model')
        return {'version':__version__,'model':ollama,'selected_model':selected,'key_present':key,'vault_error':vault_error,'system':system,'documents':len(docs),'reviewed_documents':sum(d['metadata']['verification_status']=='reviewed' for d in docs),'download':dict(self.download),'storage_path':str(self.store.folder),
                'presets':models.describe(system,ollama['models'],selected),
                'setup_choice':models.setup_choice(self.store.folder) if not selected else None,
                'ollama_setup':dict(self.ollama_setup)}

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
            if path == '/api/earnings': return earnings.overview(s)
            if path == '/api/prices': return {'prices':s.prices(),**prices.status(s)}
        if method == 'POST':
            if path == '/api/chat':
                if not self.chat_lock.acquire(blocking=False):
                    raise AppError('이전 질문을 처리 중입니다. 잠시 후 다시 시도해 주세요.',409)
                try:
                    return answer(s,self.model,data,self.nexon)
                finally:
                    self.chat_lock.release()
            if path == '/api/characters/profile': return self.nexon.character(required(data,'name',30), details=True)
            if path == '/api/characters/discover': return self.nexon.characters()
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
            if path == '/api/earnings/scheduler':
                names = [c['name'] for c in s.characters()]
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
                return {'ok':True}
            if path == '/api/settings/key/delete':
                self.vault.delete()
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
                        threading.Thread(target=self.server.shutdown,daemon=True).start()
                        close = getattr(app,'on_shutdown',None)
                        if close:   # 앱 창으로 실행 중이면 창도 닫는다.
                            threading.Thread(target=close,daemon=True).start()
                        return
                    result = app.route(self.command,path,parse_qs(parsed.query),data)
                    return self.respond(200,result)
                if self.command != 'GET': raise AppError('요청 경로를 찾을 수 없습니다.',404)
                filename = {'/':'index.html','/app.js':'app.js','/characters.js':'characters.js','/style.css':'style.css','/favicon.svg':'favicon.svg'}.get(path)
                if not filename: raise AppError('파일을 찾을 수 없습니다.',404)
                content = (STATIC/filename).read_bytes()
                self.respond(200,content,(mimetypes.guess_type(filename)[0] or 'text/plain')+'; charset=utf-8')
            except AppError as e:
                self.respond(e.status,{'error':str(e)})
            except Exception:
                self.respond(500,{'error':'처리 중 오류가 발생했습니다. 입력값과 앱 상태를 확인해 주세요.'})
        do_GET = handle_request
        do_POST = handle_request
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads = True
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
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0
