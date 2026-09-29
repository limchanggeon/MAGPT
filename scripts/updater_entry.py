"""업데이터 실행 파일(MepitiUpdater)의 시작점. 본체와 따로 빌드한다(scripts/build.py)."""
from mepiti.update_apply import main

if __name__ == '__main__':
    raise SystemExit(main())
