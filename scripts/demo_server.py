"""README 스크린샷용 데모 서버.

실제 넥슨 API·키체인·사용자 DB를 쓰지 않는다. 캐릭터 정보는 익명화한 프로필 파일
(scripts/eval_models.py가 만드는 artifacts/eval_profile.json)에서 읽고, 데이터는 임시 폴더에만 쓴다.
AI 답변은 로컬 Ollama를 그대로 쓴다.

예:
  .venv/bin/python scripts/demo_server.py --port 8790
  .venv/bin/python scripts/demo_server.py --port 8791 --first-run   # 모델을 고르지 않은 첫 실행 화면
  .venv/bin/python scripts/demo_server.py --port 8791 --first-run --no-key   # API 키도 없는 첫 실행 화면
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mepiti import conditions, server  # noqa: E402
from mepiti.adapters import CLOUD_MODEL, cloud_failure, gemini_error  # noqa: E402
from mepiti.core import AppError, now  # noqa: E402


NEXON = 'nexon-api-key'


class FakeVault:
    """키는 메모리에만 둔다. 실제 키체인을 건드리지 않는다. 넥슨 키와 Gemini 키를 이름별로 따로 둔다."""
    keys = {NEXON: 'demo-key'}
    broken = False                           # 넥슨 키 보관소를 못 읽는 상황

    def __init__(self, account=NEXON, label=''):
        self.account = account

    def get(self):
        if type(self).broken and self.account == NEXON:
            raise AppError('OS 보안 저장소에 접근하지 못했습니다.', 503)
        return type(self).keys.get(self.account)

    def save(self, key): type(self).keys[self.account] = key
    def delete(self): type(self).keys.pop(self.account, None)


class FakeGemini:
    """실제 Google을 부르지 않는 Gemini. 'AIza-bad'로 시작하는 키는 Google이 거절한 것처럼 응답한다."""
    def __init__(self, vault, real=None):
        self.vault, self.model = vault, None
        self.MODELS = getattr(real, 'MODELS', ())
        self.model_setting = getattr(real, 'model_setting', lambda: None)

    @property
    def chosen(self):
        picked = self.model_setting()
        return picked if picked in dict(self.MODELS) else (self.MODELS[0][0] if self.MODELS else None)

    def check(self, key=None):
        if (key or '').startswith('AIza-bad'):
            raise gemini_error(400, {'message': 'API key not valid. Please pass a valid API key.'})
        self.model = self.chosen or 'gemini-flash-lite-latest'
        return self.model

    def analyse(self, model, facts, question, history=None, numbers_shown=False):
        return '(데모) 클라우드 모델 자리입니다. 데모 서버는 실제 Gemini를 부르지 않습니다.', {}

    def select(self, model, question, passages):
        return [0], {}


class FakePaidCloud:
    """실제 회사를 부르지 않는 Claude·ChatGPT. 'sk-bad'로 시작하는 키는 거절한 것처럼 응답한다."""
    def __init__(self, real, name):
        self.vault, self.MODELS, self.model_setting, self.name = real.vault, real.MODELS, real.model_setting, name

    @property
    def model(self):
        chosen = self.model_setting()
        return chosen if chosen in dict(self.MODELS) else self.MODELS[0][0]

    def check(self, key=None):
        if (key or '').startswith('sk-bad'):
            raise cloud_failure('invalid', f'{self.name}가 이 API 키를 받아 주지 않았어요(데모).')
        return self.model

    def analyse(self, model, facts, question, history=None, numbers_shown=False):
        return f'(데모) {self.name} {self.model} 자리입니다. 데모 서버는 실제 회사를 부르지 않습니다.', {}

    def select(self, model, question, passages):
        return [0], {}


def fake_nexon_error(key):
    """키 'bad-key-test'는 넥슨이 거절한 것처럼, 'offline-key-test'는 인터넷이 안 되는 것처럼 응답한다."""
    if key == 'bad-key-test':
        error = AppError('요청 조건을 확인해 주세요. 넥슨 오류 코드: OPENAPI00005.', 502)
        error.upstream, error.nexon_code = 400, 'OPENAPI00005'
        return error
    if key == 'offline-key-test':
        return AppError('서비스에 연결하지 못했습니다. 인터넷 또는 로컬 모델 실행 상태를 확인해 주세요.', 503)
    return None


class FakeNexon:
    """데모에서 쓰는 캐릭터 한 명. 나머지 넥슨 기능은 데모에서 제공하지 않는다."""
    def __init__(self, profile, key=None):
        self.profile = profile
        self.key = key                     # None이면 저장된(가짜) 키를 쓴다

    def character(self, name, details=False):
        return dict(self.profile, retrieved_at=now())

    def characters(self):
        error = fake_nexon_error(self.key if self.key is not None else FakeVault.keys.get(NEXON))
        if error:
            raise error
        p = self.profile
        return {'characters': [{'name': p['name'], 'world': p.get('world'), 'job': p.get('job'), 'level': p.get('level')}],
                'retrieved_at': now(), 'source_url': 'demo'}

    def basic_on(self, name, day=None):
        # 데모용 경험치 흐름: 하루 약 5%씩 오른다(목표 탭).
        from datetime import date
        back = (date.today() - date.fromisoformat(day)).days if day else 0
        return {'level': self.profile.get('level') or 285, 'exp': int((60 - back * 5) / 100 * 3.2e14), 'rate': 60 - back * 5}

    def _unavailable(self, *args, **kwargs):
        raise AppError('데모에서는 제공하지 않는 기능입니다.', 503)

    union = scheduler = starforce_history = notices = notice_detail = _unavailable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='artifacts/eval_profile.json')
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--model', default='qwen3.5:2b')
    parser.add_argument('--first-run', action='store_true', help='모델을 고르지 않은 상태로 띄운다')
    parser.add_argument('--no-key', action='store_true', help='API 키가 없는 상태로 띄운다(키 bad-key-test는 넥슨 거절, offline-key-test는 연결 실패로 응답)')
    parser.add_argument('--bad-saved-key', action='store_true', help='넥슨이 거절하는(만료된) 키가 저장된 상태로 띄운다')
    parser.add_argument('--vault-error', action='store_true', help='보안 저장소를 읽지 못하는 상태로 띄운다')
    parser.add_argument('--cloud', action='store_true', help='클라우드(Gemini, 가짜)를 쓰는 상태로 띄운다. --first-run과 함께 쓰면 키만 없는 상태')
    args = parser.parse_args()

    profile = json.loads(Path(args.profile).read_text(encoding='utf-8'))
    folder = tempfile.mkdtemp(prefix='mepiti-demo-')
    server.Vault = FakeVault
    if args.no_key:
        FakeVault.keys.pop(NEXON, None)
    if args.bad_saved_key:
        FakeVault.keys[NEXON] = 'bad-key-test'
    FakeVault.broken = args.vault_error
    app = server.Application(folder)
    app.vault = FakeVault()
    app.nexon = FakeNexon(profile)
    app.nexon_for = lambda key: FakeNexon(profile, key)
    app.gemini = FakeGemini(app.cloud_vault, app.gemini)
    app.claude = FakePaidCloud(app.claude, 'Claude')
    app.openai = FakePaidCloud(app.openai, 'ChatGPT')
    if args.cloud and not args.first_run:
        app.cloud_vault.save('AIza-demo-key-000000000000')
        args.model = CLOUD_MODEL
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
