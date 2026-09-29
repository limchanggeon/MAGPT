"""앱 창 실행(pywebview). 실제 창은 띄우지 않고 가짜 webview로 흐름만 확인한다."""
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from mepiti import desktop
from mepiti.server import Application, make_server, main, say


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

    def start(self, *args, **kwargs):
        self.started = True
        self.start_kwargs = kwargs
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
        self.assertEqual((title, url), ('메피티', self.url + '?shell=window'))   # 화면 쪽이 앱 창임을 안다
        self.assertEqual(kw['background_color'], desktop.BACKGROUND)          # 켤 때 흰 화면이 번쩍이지 않는다
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

    def test_console_without_korean_does_not_crash(self):
        # 실제로 겪은 문제: Windows CI 콘솔(cp1252)에서 한글 안내를 출력하다 UnicodeEncodeError로 멈췄다.
        raw = io.BytesIO()
        console = io.TextIOWrapper(raw, encoding='cp1252')
        say('메피티 · http://127.0.0.1:8765', console)
        self.assertIn(b'http://127.0.0.1:8765', raw.getvalue())
        with patch('mepiti.server.sys.stdout', None):
            say('창 모드에는 콘솔이 없다')


if __name__ == '__main__':
    unittest.main()


class SingleInstanceTests(unittest.TestCase):
    """다시 실행하면 새 창을 띄우지 않고 떠 있는 창을 앞으로 가져온다."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Application(self.tmp.name)
        self.server = make_server(self.app, 0)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_running_window_is_brought_to_front(self):
        from mepiti.server import focus_existing
        called = threading.Event()
        self.app.on_focus = called.set
        self.assertTrue(focus_existing(self.port))
        self.assertTrue(called.wait(2))

    def test_browser_mode_server_is_not_a_window(self):
        from mepiti.server import focus_existing
        self.assertFalse(focus_existing(self.port))          # on_focus가 없으면 창이 아니다

    def test_nothing_listening(self):
        from mepiti.server import focus_existing
        self.assertFalse(focus_existing(1))

    def test_second_launch_does_not_open_new_window(self):
        self.app.on_focus = lambda: None
        opened = []
        with patch('mepiti.server.window_only', side_effect=lambda url: opened.append(url)):
            self.assertEqual(main(['--window', '--port', str(self.port), '--data-dir', self.tmp.name]), 0)
        self.assertEqual(opened, [])

    def test_port_in_use_is_refused(self):
        # Windows는 기본값(SO_REUSEADDR)으로 두면 같은 포트를 또 잡아서 두 번째 실행이 기존 메피티를 못 알아봤다.
        with self.assertRaises(OSError):
            make_server(self.app, self.port)

    def test_window_url_marks_app_shell(self):
        self.assertEqual(desktop.window_url('http://127.0.0.1:8765'), 'http://127.0.0.1:8765?shell=window')
        self.assertEqual(desktop.window_url('http://x/?a=1'), 'http://x/?a=1&shell=window')


class MainThreadTests(unittest.TestCase):
    """macOS에서 창을 서버 스레드에서 직접 건드리면 앱이 죽는다(실제 패키지 앱에서 두 번 종료됨).
    창 작업은 전부 AppHelper.callAfter로 메인 스레드에 넘겨야 한다."""
    def test_bring_to_front_on_mac_never_touches_window_directly(self):
        import sys, types
        touched, dispatched = [], []
        window = type('W', (), {})()
        window.restore = lambda: touched.append('restore')
        window.show = lambda: touched.append('show')
        helper = types.SimpleNamespace(callAfter=lambda fn, *a: dispatched.append(fn))
        appkit = types.SimpleNamespace(NSApplication=object)
        tools = types.ModuleType('PyObjCTools'); tools.AppHelper = helper
        with patch.object(sys, 'platform', 'darwin'), \
             patch.dict(sys.modules, {'AppKit': appkit, 'PyObjCTools': tools, 'PyObjCTools.AppHelper': helper}):
            desktop.bring_to_front(window)
        self.assertEqual(touched, [])          # 서버 스레드에서 창을 건드리지 않는다
        self.assertEqual(len(dispatched), 1)   # 메인 스레드로 넘긴다

