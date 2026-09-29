"""경매장 노작값 조회(mepiti/auction.py). 실제 넥슨은 부르지 않고, 경매장 페이지 대신 가짜 창이 응답한다."""
import json
import tempfile
import threading
import unittest

from mepiti import auction, prices
from mepiti.core import AppError, Store
from mepiti.server import Application


class FakeEvent:
    def __init__(self): self.handlers = []
    def __iadd__(self, fn): self.handlers.append(fn); return self
    def fire(self): return [fn() for fn in self.handlers]


class FakeWindow:
    """경매장 페이지 흉내. run_js로 받은 요청을 routes에 따라 답한다(다른 스레드에서, 실제 창처럼)."""
    def __init__(self, owner, routes, href=auction.AUCTION_URL + 'items'):
        self.owner, self.routes, self.href = owner, routes, href
        self.events = type('E', (), {})()
        self.events.loaded, self.events.closing, self.events.closed = FakeEvent(), FakeEvent(), FakeEvent()
        self.sent, self.hidden, self.destroyed, self.shown = [], False, False, 0
    def expose(self, *fns): self.exposed = [f.__name__ for f in fns]
    def show(self): self.shown += 1
    def hide(self): self.hidden = True
    def destroy(self): self.destroyed = True
    def run_js(self, script):
        if 'window.__mepiti({' not in script:
            self.owner.auction_page(self.href)          # 페이지 불러오기 때 심는 코드
            return
        payload = json.loads(script.split('window.__mepiti(', 1)[1].rsplit(');', 1)[0])
        self.sent.append(payload)
        path = payload['url'][len(auction.API):]
        status, body = self.routes(payload['method'], path, payload['body'])
        reply = {'href': self.href, 'status': status, 'body': json.dumps(body), 'version': '1.2.3'}
        threading.Thread(target=self.owner.auction_reply, args=(payload['id'], reply)).start()


class FakeWebview:
    def __init__(self, routes): self.routes, self.windows = routes, []
    def create_window(self, title, url, **kw):
        self.title, self.url = title, url
        w = FakeWindow(self.owner, self.routes); self.windows.append(w); return w


def market(method, path, body):
    """계정 하나, 크로아에 '테스트'(285)·'부캐'(260). 검색 결과엔 이름이 다른 매물과 더 비싼 매물이 섞여 있다."""
    if path == '/accounts':
        return 200, {'accounts': [{'accountId': 11}]}
    if path == '/accounts/11/gameWorlds/5/characters':
        return 200, {'characters': [{'characterId': 1, 'characterName': '부캐', 'level': 260},
                                    {'characterId': 2, 'characterName': '테스트', 'level': 285}]}
    if path.startswith('/accounts/11/gameWorlds/'):
        return 500, {'code': 1}                                      # 캐릭터 없는 월드
    if path == '/market/web/daily-limit':
        return 200, {'search': {'remaining': 97}}
    if path == '/market/web/items/searches/tool-tip':
        assert body['filters'] == {'exactMatch': True, 'keyword': '골든 클로버 벨트'}
        return 200, {'items': [{'itemName': '골든 클로버 벨트', 'pricePerItem': '31000000', 'isMyWorld': True},
                               {'itemName': '골든 클로버 벨트', 'pricePerItem': '29000000', 'isMyWorld': False},
                               {'itemName': '골든 클로버 벨트 (파편)', 'pricePerItem': '100', 'isMyWorld': True}],
                     'searchKey': 'k'}
    return 404, {}


class AuctionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.store = Store(self.tmp.name)
    def tearDown(self):
        prices.register_fetcher(None); self.tmp.cleanup()
    def make(self, routes=market, preferred='테스트'):
        web = FakeWebview(routes); a = auction.Auction(web, self.store, lambda: preferred); web.owner = a
        return a, web
    def opened(self, routes=market, preferred='테스트'):
        a, web = self.make(routes, preferred)
        a.open(); web.windows[0].events.loaded.fire()
        return a, web.windows[0]

    def test_needs_window_and_login(self):
        a, _ = self.make()
        with self.assertRaises(AppError): a.request('GET', '/accounts')
        a, w = self.opened()
        a.auction_page('https://nxlogin.nexon.com/auth/login')      # 아직 로그인 화면
        with self.assertRaises(AppError) as e: a.request('GET', '/accounts')
        self.assertIn('로그인', str(e.exception)); self.assertEqual(w.sent, [])

    def test_open_uses_own_window_and_bridge(self):
        a, web = self.make()
        a.open(); a.open()
        self.assertEqual(len(web.windows), 1); self.assertEqual(web.windows[0].shown, 1)   # 두 번째는 앞으로만
        self.assertEqual(web.url, auction.AUCTION_URL)
        self.assertEqual(sorted(web.windows[0].exposed), ['auction_page', 'auction_reply'])

    def test_cheapest_exact_name_with_preferred_character(self):
        a, w = self.opened()
        found = a.fetch('골든 클로버 벨트')
        self.assertEqual(found['price'], 29000000)                    # 다른 이름(파편)은 빼고 가장 싼 것
        self.assertIn('다른 월드 매물', found['note']); self.assertIn('크로아', found['note'])
        search = next(p for p in w.sent if p['url'].endswith('/searches/tool-tip'))
        self.assertEqual((search['body']['characterId'], search['body']['worldId']), (2, 5))
        self.assertEqual(a.remaining, 97); self.assertEqual(a.version, '1.2.3')
        self.assertEqual(len(a.store.setting(auction.DEVICE_SETTING)), 32)

    def test_highest_level_when_main_character_is_elsewhere(self):
        a, _ = self.opened(preferred='없는캐릭')
        self.assertEqual(a.find_identity()['name'], '테스트')

    def test_session_lost_and_version_rejected(self):
        a, _ = self.opened(lambda m, p, b: (401, {}))
        with self.assertRaises(AppError) as e: a.fetch('벨트')
        self.assertIn('다시 로그인', str(e.exception))
        a, _ = self.opened(lambda m, p, b: (426, {'code': 6013}))
        with self.assertRaises(AppError) as e: a.request('GET', '/accounts')
        self.assertIn('426', str(e.exception))

    def test_check_reports_state_without_searching(self):
        a, w = self.opened()
        st = a.check()
        self.assertEqual((st['character'], st['world'], st['remaining'], st['error']), ('테스트', '크로아', 97, None))
        self.assertFalse(any(p['url'].endswith('/searches/tool-tip') for p in w.sent))    # 검색 횟수를 쓰지 않는다

    def test_close_hides_until_app_quits(self):
        a, w = self.opened()
        self.assertEqual(w.events.closing.fire(), [False]); self.assertTrue(w.hidden)
        a.shutdown(); self.assertTrue(w.destroyed)
        self.assertEqual(w.events.closing.fire(), [True])

    def test_prices_use_auction_and_explain_failure(self):
        a, _ = self.opened()
        prices.register_fetcher(a.fetch); self.store.set_setting(prices.FETCH_SETTING, '1')
        got = prices.resolve(self.store, '골든 클로버 벨트')
        self.assertEqual((got['known'], got['price'], got['source']), (True, 29000000, 'auction'))
        self.assertTrue(prices.resolve(self.store, '골든 클로버 벨트')['known'])      # 저장된 값, 다시 검색 안 함
        a.auction_page('https://nxlogin.nexon.com/auth/login')
        miss = prices.resolve(self.store, '다른 벨트')
        self.assertEqual(miss['reason'], 'fetch_error'); self.assertIn('경매장 조회 실패', prices.ask_text([miss]))


class AuctionRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.app = Application(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_browser_mode_has_no_auction(self):
        st = self.app.route('GET', '/api/auction/status', {}, {})
        self.assertFalse(st['available']); self.assertIn('앱 창', st['reason'])
    def test_successful_check_turns_on_price_lookup(self):
        web = FakeWebview(market); a = auction.Auction(web, self.app.store, lambda: '테스트'); web.owner = a
        self.app.auction = a
        self.app.route('POST', '/api/auction/open', {}, {}); web.windows[0].events.loaded.fire()
        st = self.app.route('POST', '/api/auction/check', {}, {})
        self.assertEqual(st['character'], '테스트')
        self.assertEqual(self.app.store.setting(prices.FETCH_SETTING), '1')


if __name__ == '__main__':
    unittest.main()
