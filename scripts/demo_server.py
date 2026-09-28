"""README 스크린샷용 데모 서버.

실제 넥슨 API·키체인·사용자 DB를 쓰지 않는다. 캐릭터 정보는 익명화한 프로필 파일
(scripts/eval_models.py가 만드는 artifacts/eval_profile.json)에서 읽고, 데이터는 임시 폴더에만 쓴다.
AI 답변은 로컬 Ollama를 그대로 쓴다.

예:
  .venv/bin/python scripts/demo_server.py --port 8790
  .venv/bin/python scripts/demo_server.py --port 8791 --first-run   # 모델을 고르지 않은 첫 실행 화면
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mepiti import conditions, server  # noqa: E402
from mepiti.core import AppError, now  # noqa: E402


class FakeVault:
    def get(self): return 'demo-key'
    def save(self, key): pass
    def delete(self): pass


class FakeNexon:
    """데모에서 쓰는 캐릭터 한 명. 나머지 넥슨 기능은 데모에서 제공하지 않는다."""
    def __init__(self, profile):
        self.profile = profile

    def character(self, name, details=False):
        return dict(self.profile, retrieved_at=now())

    def characters(self):
        p = self.profile
        return {'characters': [{'name': p['name'], 'world': p.get('world'), 'job': p.get('job'), 'level': p.get('level')}],
                'retrieved_at': now(), 'source_url': 'demo'}

    def _unavailable(self, *args, **kwargs):
        raise AppError('데모에서는 제공하지 않는 기능입니다.', 503)

    union = scheduler = starforce_history = notices = notice_detail = _unavailable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='artifacts/eval_profile.json')
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--model', default='qwen3.5:2b')
    parser.add_argument('--first-run', action='store_true', help='모델을 고르지 않은 상태로 띄운다')
    args = parser.parse_args()

    profile = json.loads(Path(args.profile).read_text(encoding='utf-8'))
    folder = tempfile.mkdtemp(prefix='mepiti-demo-')
    server.Vault = FakeVault
    app = server.Application(folder)
    app.vault = FakeVault()
    app.nexon = FakeNexon(profile)
    app.token = 'demo-token'
    store = app.store
    store.character_save({'name': profile['name'], 'budget': 30_000_000_000, 'goal': '검은 마법사 클리어', 'main': True})
    if not args.first_run:
        store.set_setting('model', args.model)
        conditions.save(store, {**conditions.DEFAULTS, 'event': '샤타포스'})
        hat = next((e for e in profile.get('equipment') or [] if e.get('slot') == '모자'), None)
        if hat:
            store.price_save({'item': hat['name'], 'price': 1_000_000_000, 'source': 'user', 'note': '데모 예시 값'})
    httpd = server.make_server(app, args.port)
    print(f'데모 서버 http://127.0.0.1:{args.port} (데이터 {folder})', flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    main()
