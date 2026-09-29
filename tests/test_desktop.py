"""앱 창 실행(pywebview). 실제 창은 띄우지 않고 가짜 webview로 흐름만 확인한다."""
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from mepiti import desktop
from mepiti.server import Application, make_server, main


class FakeWebview:
    def __init__(self, fail=False, on_start=None):
        self.settings = {}
        self.windows = []
        self.fail = fail
        self.on_start = on_start
        self.started = False

    def create_window(self, title, url, **kw):
        if self.fail:
            raise RuntimeError('no display')
        window = type('W', (), {'destroyed': False})()
        window.destroy = lambda: setattr(window, 'destroyed', True)
        self.windows.append((title, url, kw, window))
        return window

    def start(self):
        self.started = True
        if self.on_start:
            self.on_start()


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(self.tmp.name)
        self.server = make_server(self.app, 0)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.server_close()
        self.tmp.cleanup()

    def test_window_serves_app_and_stops_server_when_closed(self):
        seen = {}

        def while_open():
            # 창이 떠 있는 동안 같은 서버가 화면을 준다.
            with urlopen(self.url + '/') as r:
                seen['page'] = '메피티' in r.read().decode()
        fake = FakeWebview(on_start=while_open)
        self.assertTrue(desktop.run(self.server, self.app, self.url, fake))
        self.assertTrue(fake.started and seen['page'])
        title, url, kw, _ = fake.windows[0]
        self.assertEqual((title, url), ('메피티', self.url))
        self.assertTrue(fake.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'])   # 외부 링크는 브라우저로

    def test_quit_button_closes_window(self):
        def press_quit():
            with urlopen(self.url + '/api/bootstrap') as r:
                token = json.loads(r.read())['token']
            req = Request(self.url + '/api/shutdown', data=b'{}', method='POST',
                          headers={'Content-Type': 'application/json', 'X-Mepiti-Token': token})
            with urlopen(req):
                pass
            for _ in range(50):          # 창 닫기는 다른 스레드에서 한다
                if fake.windows[0][3].destroyed:
                    break
                threading.Event().wait(0.02)
        fake = FakeWebview(on_start=press_quit)
        desktop.run(self.server, self.app, self.url, fake)
        self.assertTrue(fake.windows[0][3].destroyed)

    def test_falls_back_when_window_cannot_open(self):
        self.assertFalse(desktop.run(self.server, self.app, self.url, FakeWebview(fail=True)))
        with patch('mepiti.desktop.load_webview', return_value=None):
            self.assertFalse(desktop.run(self.server, self.app, self.url))

    def test_main_window_flag_uses_desktop(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch('mepiti.server.desktop.run', return_value=True) as run, \
                patch('mepiti.server.webbrowser.open') as browser:
            self.assertEqual(main(['--window', '--port', '0', '--data-dir', folder]), 0)
        run.assert_called_once()
        browser.assert_not_called()


if __name__ == '__main__':
    unittest.main()
