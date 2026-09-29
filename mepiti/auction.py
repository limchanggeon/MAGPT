"""경매장 노작값 조회 — 메피티 안의 창에서 넥슨 웹 경매장에 로그인해 쓴다.

출처: maple-auction-mcp (https://github.com/oyc0401/maple-auction-mcp, MIT License, Copyright (c) 2026 oyc0401)의
경매장 요청 방식(엔드포인트, 요청 본문, 헤더, 계정·캐릭터 찾기)을 참고해 파이썬으로 옮겼다.
그 프로젝트는 크롬 확장이 로그인된 크롬에서 요청을 대신 보낸다. 메피티는 확장 대신 자기 창(pywebview)에
경매장 페이지를 열고, 그 페이지 안에서 요청을 보낸다. 그래서 크롬·확장·Node가 필요 없다.

- 쿠키는 브라우저 엔진이 알아서 붙인다. 앱은 쿠키를 읽지도 저장하지도 않는다.
- 웹 경매장이 요구하는 헤더(x-client-version 등)는 고정하지 않는다. 경매장 페이지가 실제로 보내는 값을
  페이지 안에서 받아 쓴다. 원작의 고정값('1.0.1')은 2026-09-29에 426으로 거절됐다(로그인 전 시험).
- 공식 Open API가 아니다. 약관 위험은 사용자가 감수하기로 했다(2026-09-29). 설정에서 켠 사람만 쓴다.
- 경매장 검색은 넥슨 쪽 한도(하루 100회)가 있다. 찾은 값은 prices가 저장해 다시 검색하지 않는다.
"""
import json
import re
import secrets
import threading
import uuid

from .core import AppError, now

AUCTION_URL = 'https://auction.maplestory.nexon.com/'
API = 'https://api.mskr.nexon.com/v1'
TITLE = '메피티 · 경매장'
DEVICE_SETTING = 'auction_device_id'
# 월드 번호(원작 constants.ts, 2026-07 실측). 캐릭터 목록은 월드별로 따로 물어야 한다.
WORLDS = {0: '스카니아', 1: '베라', 3: '루나', 4: '제니스', 5: '크로아', 10: '유니온', 16: '엘리시움', 29: '이노시스',
          43: '레드', 44: '오로라', 45: '에오스', 46: '헬리오스', 48: '챌린저스2', 49: '챌린저스', 50: '아케인',
          51: '노바', 52: '챌린저스3', 54: '챌린저스4'}
# 페이지가 보내는 헤더 중 가져다 쓸 것. 인증 성격의 헤더는 받지 않는다.
KEEP_HEADERS = ('x-client-version', 'x-platform', 'x-device-id')

# 경매장 페이지에 심는 코드. 페이지가 API에 보내는 헤더 중 KEEP_HEADERS만 기억하고,
# 메피티가 요청을 맡기면 그 헤더로 fetch한 결과를 메피티로 돌려준다(window.pywebview.api.auction_reply).
PAGE_JS = r"""
(() => {
  if (window.__mepiti) return;
  const keep = %s;
  const seen = window.__mepitiHeaders = {};
  const isApi = (u) => String(u || '').includes('api.mskr.nexon.com');
  const remember = (k, v) => { k = String(k).toLowerCase(); if (keep.includes(k)) seen[k] = String(v); };
  const originalFetch = window.fetch;
  window.fetch = function (input, init) {
    try {
      const url = typeof input === 'string' ? input : input && input.url;
      if (isApi(url)) new Headers((init && init.headers) || (input && input.headers) || {}).forEach((v, k) => remember(k, v));
    } catch (e) {}
    return originalFetch.apply(this, arguments);
  };
  const open = XMLHttpRequest.prototype.open, setHeader = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function (m, u) { this.__api = isApi(u); return open.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) { if (this.__api) remember(k, v); return setHeader.apply(this, arguments); };
  // 페이지가 아직 API를 부르지 않았을 때를 위해, 경매장 스크립트에 적힌 클라이언트 버전을 찾아 둔다.
  window.__mepitiVersion = null;
  (async () => {
    for (const s of document.querySelectorAll('script[src]')) {
      try {
        if (new URL(s.src).origin !== location.origin) continue;
        const text = await (await originalFetch(s.src)).text();
        const m = text.match(/x-client-version["'`]?\s*[:,]\s*["'`]([0-9]+(?:\.[0-9]+){1,3})/i);
        if (m) { window.__mepitiVersion = m[1]; break; }
      } catch (e) {}
    }
  })();
  window.__mepiti = async (p) => {
    const reply = (value) => window.pywebview.api.auction_reply(p.id, Object.assign({href: location.href}, value));
    try {
      const h = Object.assign({'accept': 'application/json, text/plain, */*', 'x-platform': 'PC_WEB', 'x-device-id': p.device}, seen);
      if (!h['x-client-version'] && window.__mepitiVersion) h['x-client-version'] = window.__mepitiVersion;
      if (p.body !== null) h['content-type'] = 'application/json';
      const r = await originalFetch(p.url, {method: p.method, credentials: 'include', headers: h,
                                            body: p.body === null ? undefined : JSON.stringify(p.body)});
      const text = await r.text();
      reply({status: r.status, body: text.slice(0, 3000000), version: h['x-client-version'] || null});
    } catch (e) {
      reply({error: String(e)});
    }
  };
})();
""" % json.dumps(list(KEEP_HEADERS))


def request_script(payload):
    """경매장 페이지에서 실행할 요청. 심어 둔 코드가 없으면(새로 불러온 페이지) 먼저 심는다."""
    return PAGE_JS + '\nwindow.__mepiti(' + json.dumps(payload, ensure_ascii=False) + ');'


def search_body(identity, keyword):
    """판매 중 검색(가격 낮은 순 10개). 원작 mapping.ts buildCreateBody의 이름 정확 일치 경우."""
    return {'worldId': identity['world_id'], 'accountId': identity['account_id'],
            'characterId': identity['character_id'], 'page': 1, 'limit': 10,
            'sortType': 'PRICE_PER_ITEM_ASC', 'saveRecentKeyword': False,
            'filters': {'exactMatch': True, 'keyword': keyword}}


def cheapest(data, item):
    """검색 결과에서 이름이 정확히 같은 가장 싼 매물."""
    rows = [r for r in (data or {}).get('items') or [] if isinstance(r, dict) and r.get('itemName') == item]
    priced = []
    for r in rows:
        try:
            priced.append((int(float(r.get('pricePerItem') or r.get('price'))), r))
        except (TypeError, ValueError):
            continue
    return min(priced, key=lambda p: p[0]) if priced else None


class Auction:
    """경매장 창 하나와 그 창을 통한 요청. 서버 스레드에서 불린다(창 조작은 pywebview가 메인 스레드로 넘긴다)."""

    def __init__(self, webview, store, preferred_character=None):
        self.webview = webview
        self.store = store
        self.preferred_character = preferred_character or (lambda: None)
        self.window = None
        self.href = ''
        self.identity = None
        self.version = None
        self.remaining = None
        self.error = None
        self.quitting = False
        self.pending = {}
        self.lock = threading.Lock()

    # ---- 창 ----
    def open(self):
        """경매장 창을 연다(이미 있으면 앞으로). 로그인은 사용자가 이 창에서 직접 한다."""
        if self.window is not None:
            self.window.show()
            return self.status()
        window = self.webview.create_window(TITLE, AUCTION_URL, width=1100, height=820, min_size=(720, 560))
        window.expose(self.auction_reply, self.auction_page)
        window.events.loaded += self._loaded
        window.events.closing += self._closing
        window.events.closed += self._closed
        self.window = window
        return self.status()

    def _loaded(self):
        # 페이지가 바뀔 때마다(로그인 → 경매장) 심고, 지금 주소를 알려 달라고 한다.
        try:
            # 페이지를 막 불러온 때는 메피티로 돌려보내는 통로(window.pywebview)가 아직 없을 수 있다. 준비되면 알린다.
            self.window.run_js(PAGE_JS + """
(() => { const tell = () => window.pywebview.api.auction_page(location.href);
  if (window.pywebview && window.pywebview.api && window.pywebview.api.auction_page) tell();
  else window.addEventListener('pywebviewready', tell, {once: true}); })();""")
        except Exception:
            pass

    def _closing(self):
        # 닫기를 누르면 숨기기만 한다. 창이 있어야 로그인이 유지되고 검색을 맡길 수 있다. 앱을 끌 때는 닫는다.
        if self.quitting:
            return True
        self.window.hide()
        return False

    def _closed(self):
        self.window, self.href, self.identity = None, '', None

    def shutdown(self):
        self.quitting = True
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass

    def auction_page(self, href):
        self.href = str(href or '')[:300]

    def auction_reply(self, request_id, value):
        with self.lock:
            waiting = self.pending.get(request_id)
        if waiting:
            waiting['value'] = value if isinstance(value, dict) else {'error': 'bad reply'}
            waiting['event'].set()

    @property
    def on_auction_page(self):
        return self.href.startswith(AUCTION_URL.rstrip('/'))

    # ---- 요청 ----
    def device_id(self):
        device = self.store.setting(DEVICE_SETTING)
        if not device:
            device = secrets.token_hex(16)          # 웹 경매장이 기기마다 보내는 임의 값. 인증 정보가 아니다.
            self.store.set_setting(DEVICE_SETTING, device)
        return device

    def request(self, method, path, body=None, timeout=20):
        if self.window is None:
            raise AppError('설정 → 장비 노작값 → 경매장 창 열기로 경매장에 먼저 로그인해 주세요.', 409)
        if not self.on_auction_page:
            raise AppError('경매장 창에서 넥슨 로그인을 마쳐 주세요. 로그인 후 경매장 화면이 보이면 됩니다.', 409)
        request_id = uuid.uuid4().hex
        waiting = {'event': threading.Event(), 'value': None}
        with self.lock:
            self.pending[request_id] = waiting
        try:
            self.window.run_js(request_script({'id': request_id, 'method': method, 'url': API + path,
                                               'body': body, 'device': self.device_id()}))
            if not waiting['event'].wait(timeout):
                raise AppError('경매장 창이 응답하지 않았습니다. 창이 열려 있는지 확인하고 다시 해 주세요.', 504)
        finally:
            with self.lock:
                self.pending.pop(request_id, None)
        value = waiting['value']
        if value.get('href'):
            self.href = str(value['href'])[:300]
        if value.get('version'):
            self.version = value['version']
        if value.get('error'):
            raise AppError('경매장 요청이 실패했습니다: ' + str(value['error'])[:200], 502)
        status = value.get('status')
        try:
            data = json.loads(value.get('body') or 'null')
        except ValueError:
            data = None
        if status in (401, 403):
            self.identity = None
            raise AppError('경매장 로그인이 풀렸습니다. 경매장 창에서 다시 로그인해 주세요. '
                           '(크롬·휴대폰 등 다른 곳에서 경매장을 열면 메피티 쪽 로그인이 풀릴 수 있습니다.)', 409)
        if status == 426:
            raise AppError('웹 경매장이 버전 확인에서 요청을 거절했습니다(426). 경매장 창에서 화면을 한 번 새로 보면 '
                           '최신 값을 다시 읽습니다. 계속되면 알려 주세요.', 502)
        if status == 422 and isinstance(data, dict) and data.get('code') == 4040:
            raise AppError('검색 결과가 너무 많아 경매장이 검색을 거부했습니다.', 502)
        if not status or status >= 400:
            code = f", 코드 {data.get('code')}" if isinstance(data, dict) and data.get('code') is not None else ''
            raise AppError(f'경매장 요청이 실패했습니다(HTTP {status}{code}).', 502)
        return data

    # ---- 신원·한도·검색 ----
    def find_identity(self):
        """검색에 쓸 캐릭터. 메피티 대표 캐릭터와 이름이 같으면 그 캐릭터, 없으면 가장 높은 레벨.

        원작 characters.ts와 같은 순서(계정 → 월드별 캐릭터). 세션을 새로 만드는 요청(web-token/session)은
        브라우저 로그인을 풀 수 있어(원작 주석) 쓰지 않는다. 계정·캐릭터 번호는 메모리에만 둔다.
        """
        if self.identity:
            return self.identity
        accounts = (self.request('GET', '/accounts') or {}).get('accounts') or []
        found = []
        for account in accounts:
            for world_id, world in WORLDS.items():
                try:
                    data = self.request('GET', f"/accounts/{account['accountId']}/gameWorlds/{world_id}/characters")
                except AppError:
                    continue                                 # 캐릭터가 없는 월드는 오류로 온다
                for c in (data or {}).get('characters') or []:
                    found.append({'account_id': account['accountId'], 'world_id': world_id, 'world': world,
                                  'character_id': c.get('characterId'), 'name': c.get('characterName'),
                                  'level': c.get('level') or 0})
        if not found:
            raise AppError('경매장 계정에서 메이플 캐릭터를 찾지 못했습니다.', 404)
        preferred = self.preferred_character()
        self.identity = next((c for c in found if c['name'] == preferred), None) or max(found, key=lambda c: c['level'])
        return self.identity

    def check(self):
        """로그인·버전·남은 검색 횟수를 확인한다. 검색 횟수를 쓰지 않는다."""
        self.error = None
        try:
            limit = self.request('GET', '/market/web/daily-limit') or {}
            self.remaining = (limit.get('search') or {}).get('remaining')
            self.find_identity()
        except AppError as e:
            self.error = str(e)
        return self.status()

    def fetch(self, item, add_grade=None):
        """prices의 외부 조회기. 판매 중 매물 중 이름이 같은 가장 싼 값(스페어용 노작값)."""
        identity = self.find_identity()
        data = self.request('POST', '/market/web/items/searches/tool-tip', search_body(identity, item))
        best = cheapest(data, item)
        try:
            limit = self.request('GET', '/market/web/daily-limit') or {}
            self.remaining = (limit.get('search') or {}).get('remaining')
        except AppError:
            pass
        if not best:
            return None
        price, row = best
        where = '' if row.get('isMyWorld', True) else ' · 다른 월드 매물'
        return {'price': price, 'source': 'auction',
                'note': f"경매장 판매 중 최저가 ({identity['world']}, {now()[:16].replace('T', ' ')}){where}"}

    def status(self):
        identity = self.identity or {}
        return {'available': True, 'open': self.window is not None, 'logged_in': self.on_auction_page,
                'character': identity.get('name'), 'world': identity.get('world'),
                'remaining': self.remaining, 'version': self.version, 'error': self.error}
