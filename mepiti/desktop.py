"""앱 창으로 실행하기.

pywebview로 운영체제의 웹 화면(macOS WKWebView, Windows WebView2)을 창에 넣고, 같은 로컬 서버 화면을 띄운다.
창을 닫으면 서버도 끈다. 설정의 '로컬 앱 종료'를 누르면 창도 닫는다.
pywebview가 없거나 창을 만들 수 없으면 지금처럼 기본 브라우저로 연다.

'창에 웹페이지를 띄운 것'처럼 보이지 않게 하는 처리도 여기서 한다.
  - 창 바탕을 앱 색으로 칠해 켤 때 흰 화면이 번쩍이지 않게 한다.
  - macOS는 제목 표시줄을 앱 색으로, Windows는 어두운 제목 표시줄로 맞춘다.
  - 이미 떠 있는데 다시 실행하면 새 창을 띄우지 않고 떠 있는 창을 앞으로 가져온다(server.main).
  - 화면 쪽(새로고침·오른쪽 클릭 메뉴·끌기·튕김 스크롤 막기)은 주소의 ?shell=window를 보고 app.js·style.css가 한다.
"""
import sys
import threading
import webbrowser
from pathlib import Path

from . import auction, prices

TITLE = '메피티'
BACKGROUND = '#0e1217'                       # style.css --bg와 같은 색
ICON = Path(__file__).with_name('static') / 'icon.png'


def close_splash():
    """Windows 패키지 앱의 로딩 창(PyInstaller 스플래시)을 닫는다. 없으면(소스 실행·macOS) 아무것도 안 한다."""
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass


def load_webview():
    try:
        import webview
    except Exception:
        return None
    return webview


def window_url(url):
    """앱 창에서 연 화면임을 화면 쪽에 알린다."""
    return url + ('&' if '?' in url else '?') + 'shell=window'


def style_native(window):
    """제목 표시줄을 앱과 같은 어두운 색으로 맞춘다. 실패해도 창은 그대로 쓴다."""
    try:
        if sys.platform == 'darwin':
            from AppKit import NSApplication, NSAppearance, NSColor
            from PyObjCTools import AppHelper

            def apply():
                app = NSApplication.sharedApplication()
                app.setAppearance_(NSAppearance.appearanceNamed_('NSAppearanceNameDarkAqua'))
                colour = NSColor.colorWithSRGBRed_green_blue_alpha_(0x0e / 255, 0x12 / 255, 0x17 / 255, 1.0)
                for native in app.windows():
                    native.setTitlebarAppearsTransparent_(True)
                    native.setBackgroundColor_(colour)
            AppHelper.callAfter(apply)
        elif sys.platform == 'win32':
            import ctypes
            handle = window.native.Handle
            hwnd = handle.ToInt64() if hasattr(handle, 'ToInt64') else int(handle)
            dark = ctypes.c_int(1)
            # DWMWA_USE_IMMERSIVE_DARK_MODE: Windows 10 20H1 이후 20, 그 전 빌드는 19
            for attribute in (20, 19):
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attribute, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                    break
    except Exception:
        pass


def bring_to_front(window):
    """다시 실행했을 때 떠 있는 창을 앞으로 가져온다.

    서버 요청 스레드에서 불린다. macOS AppKit은 창을 메인 스레드에서만 다룰 수 있어서, pywebview의
    window.restore()를 여기서 부르면 앱이 죽는다(2026-09-29 실제 패키지 앱에서 'Must only be used from the
    main thread'로 두 번 종료됨). macOS는 모든 창 작업을 메인 스레드로 넘긴다.
    """
    if sys.platform == 'darwin':
        try:
            from AppKit import NSApplication
            from PyObjCTools import AppHelper

            def front():
                app = NSApplication.sharedApplication()
                for native in app.windows():
                    if native.title() != TITLE:
                        continue
                    if native.isMiniaturized():
                        native.deminiaturize_(None)
                    native.makeKeyAndOrderFront_(None)
                app.activateIgnoringOtherApps_(True)
            AppHelper.callAfter(front)
        except Exception:
            pass
        return
    for step in (window.restore, window.show):
        try:
            step()
        except Exception:
            pass
    try:
        # 다른 창 뒤에 있을 때 앞으로 올리는 가장 확실한 방법: 잠깐 항상 위로 두었다 푼다.
        window.on_top = True
        window.on_top = False
    except Exception:
        pass


def run(server, app, url, webview=None):
    """창을 띄우고 닫힐 때까지 기다린다. 창을 못 만들면 False를 돌려주고, 호출한 쪽이 브라우저로 연다."""
    webview = webview or load_webview()
    if webview is None:
        return False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # 외부 링크(공지 등)는 앱 창이 아니라 기본 브라우저로 연다.
        settings = getattr(webview, 'settings', None)
        if isinstance(settings, dict):
            settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        window = webview.create_window(
            TITLE, window_url(url), width=1440, height=940, min_size=(960, 640),
            background_color=BACKGROUND,   # 기본은 흰색이라 켤 때 번쩍인다
            text_select=True,              # 어디를 고를 수 있는지는 style.css가 정한다(답변 글·입력칸만)
            zoomable=False)
        app.on_shutdown = lambda: window.destroy()
        app.on_focus = lambda: bring_to_front(window)
        # 경매장은 앱 창 안의 두 번째 창에서 로그인해 쓴다(mepiti/auction.py). 노작값 조회기로 붙인다.
        app.auction = auction.Auction(webview, app.store, app.main_character_name)
        prices.register_fetcher(app.auction.fetch)
        events = getattr(window, 'events', None)
        if events is not None and hasattr(events, 'shown'):
            events.shown += lambda: style_native(window)
            events.shown += close_splash                # 메피티 창이 뜨면 로딩 창을 닫는다
        if events is not None and hasattr(events, 'closed'):
            events.closed += app.auction.shutdown       # 숨겨 둔 경매장 창이 남아 있으면 앱이 끝나지 않는다
        # macOS는 메인 스레드에서 돌아야 한다. 창이 닫힐 때까지 멈춰 있다.
        # private_mode=False: 경매장 로그인을 앱을 다시 켜도 유지한다(쿠키는 웹 엔진 저장소에만 있고 앱은 읽지 않는다).
        webview.start(icon=str(ICON) if ICON.exists() else None, private_mode=False,
                      storage_path=str(Path(app.store.folder) / 'webview'))
    except Exception:
        close_splash()
        server.shutdown()
        thread.join(timeout=5)
        prices.register_fetcher(None)
        return False
    server.shutdown()
    thread.join(timeout=5)
    prices.register_fetcher(None)
    return True


def open_browser(url):
    webbrowser.open(url)
