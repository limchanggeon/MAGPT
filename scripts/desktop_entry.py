"""패키지 앱의 시작점. 기본은 앱 창으로 열고, 창을 만들 수 없으면 브라우저로 연다."""
import sys

from mepiti.server import main

if __name__ == '__main__':
    raise SystemExit(main(['--window', *sys.argv[1:]]))
