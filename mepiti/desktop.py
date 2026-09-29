"""앱 창으로 실행하기.

pywebview로 운영체제의 웹 화면(macOS WKWebView, Windows WebView2)을 창에 넣고, 같은 로컬 서버 화면을 띄운다.
창을 닫으면 서버도 끈다. 설정의 '로컬 앱 종료'를 누르면 창도 닫는다.
pywebview가 없거나 창을 만들 수 없으면 지금처럼 기본 브라우저로 연다.
"""
import threading
import webbrowser

TITLE = '메피티'


def load_webview():
    try:
        import webview
    except Exception:
        return None
    return webview


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
        window = webview.create_window(TITLE, url, width=1440, height=940, min_size=(960, 640))
        app.on_shutdown = lambda: window.destroy()
        webview.start()          # macOS는 메인 스레드에서 돌아야 한다. 창이 닫힐 때까지 멈춰 있다.
    except Exception:
        server.shutdown()
        thread.join(timeout=5)
        return False
    server.shutdown()
    thread.join(timeout=5)
    return True


def open_browser(url):
    webbrowser.open(url)
