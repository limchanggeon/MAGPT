"""새 버전 확인과 업데이트(본체 쪽). 실제로 파일을 바꾸는 일은 따로 빌드한 업데이터(update_apply)가 한다.

릴리스에는 파일이 두 벌 올라간다.
  - 처음 설치용: Mepiti-Windows-Setup.exe(설치 마법사) / Mepiti-macOS.dmg
  - 업데이트용:   Mepiti-Windows-update.zip / Mepiti-macOS-update.zip(앱 파일만) + SHA256SUMS.txt
업데이트: GitHub 최신 릴리스 확인 → 이 OS의 업데이트 zip을 받아 SHA256 확인 → 업데이터를 임시 폴더로 복사해
실행 → 메피티 종료 → 업데이터가 파일을 바꾸고 다시 켠다. 데이터·키는 설치 폴더 밖이라 그대로다.
설치 파일로 만든 앱(패키지)에서만 한다. 소스에서 실행 중이면 확인만 한다.
"""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import __version__
from .core import AppError

REPO = 'limchanggeon/MAGPT'
RELEASES = f'https://github.com/{REPO}/releases/latest'
UPDATE_ASSETS = {'Darwin': 'Mepiti-macOS-update.zip', 'Windows': 'Mepiti-Windows-update.zip'}
SUMS = 'SHA256SUMS.txt'


def latest_url():
    # 시험할 때는 MEPITI_UPDATE_URL로 가짜 릴리스 정보를 줄 수 있다.
    return os.environ.get('MEPITI_UPDATE_URL') or f'https://api.github.com/repos/{REPO}/releases/latest'


def version_tuple(text):
    numbers = re.findall(r'\d+', str(text or '').split('-')[0])
    return tuple(int(n) for n in numbers[:3]) + (0,) * (3 - len(numbers[:3]))


def fetch(url, timeout=20):
    request = Request(url, headers={'User-Agent': f'Mepiti/{__version__}', 'Accept': 'application/vnd.github+json'})
    return urlopen(request, timeout=timeout)


def install_layout():
    """패키지 앱이면 (업데이트 항목이 들어갈 폴더, 다시 켤 대상, 업데이터 실행 파일). 소스 실행이면 None."""
    if not getattr(sys, 'frozen', False):
        return None
    exe = Path(sys.executable).resolve()
    if platform.system() == 'Darwin':
        app = exe.parents[2]                                  # Mepiti.app/Contents/MacOS/Mepiti
        if app.suffix != '.app':
            return None
        return app.parent, app, exe.parent / 'MepitiUpdater'
    return exe.parent, exe, exe.parent / 'MepitiUpdater.exe'


def parse_sums(text):
    sums = {}
    for line in str(text).splitlines():
        found = re.match(r'([0-9a-fA-F]{64})\s+\*?(.+)', line.strip())
        if found:
            sums[found[2].strip()] = found[1].lower()
    return sums


class Updater:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.info = None
        self.state = {'running': False, 'status': None}
        self.lock = threading.Lock()

    def check(self):
        """최신 릴리스를 확인한다. 네트워크가 안 되면 오류 대신 상태로 알린다."""
        try:
            with fetch(latest_url()) as response:
                release = json.loads(response.read(2_000_000))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as e:
            self.info = {'current': __version__, 'error': f'새 버전을 확인하지 못했어요({e.__class__.__name__}).',
                         'page': RELEASES}
            return self.info
        latest = str(release.get('tag_name') or '').lstrip('v')
        assets = {a.get('name'): a.get('browser_download_url') for a in release.get('assets') or [] if isinstance(a, dict)}
        wanted = UPDATE_ASSETS.get(platform.system())
        layout = install_layout()
        reason = None
        if layout is None:
            reason = '소스에서 실행 중이라 자동 업데이트를 하지 않습니다.'
        elif not wanted or wanted not in assets or SUMS not in assets:
            reason = '이 버전에는 자동 업데이트 파일이 없어요. 릴리스 페이지에서 받아 설치해 주세요.'
        elif not layout[2].exists():
            reason = '업데이터가 없는 설치예요. 릴리스 페이지에서 받아 설치해 주세요.'
        elif not os.access(layout[0], os.W_OK):
            reason = f'{layout[0]} 폴더에 쓸 권한이 없어 자동 업데이트를 할 수 없어요. 릴리스 페이지에서 받아 설치해 주세요.'
        self.info = {'current': __version__, 'latest': latest,
                     'newer': version_tuple(latest) > version_tuple(__version__),
                     'notes': str(release.get('body') or '')[:3000], 'page': release.get('html_url') or RELEASES,
                     'can_apply': reason is None, 'reason': reason,
                     '_zip': assets.get(wanted), '_sums': assets.get(SUMS), '_name': wanted}
        return self.info

    def public(self):
        info = {k: v for k, v in (self.info or {}).items() if not k.startswith('_')}
        return {**info, 'state': dict(self.state)}

    def start(self, quit_app):
        """업데이트를 뒤에서 진행한다. 다 되면 업데이터를 띄우고 quit_app()으로 메피티를 끈다."""
        info = self.info or self.check()
        if not info.get('newer'):
            raise AppError('이미 최신 버전이에요.')
        if not info.get('can_apply'):
            raise AppError(info.get('reason') or '자동 업데이트를 할 수 없어요.')
        with self.lock:
            if self.state['running']:
                raise AppError('이미 업데이트하고 있어요.', 409)
            self.state = {'running': True, 'status': '업데이트 파일 받는 중', 'completed': 0, 'total': None}
        threading.Thread(target=self._run, args=(info, quit_app), daemon=True).start()
        return self.public()

    def _run(self, info, quit_app):
        try:
            folder = self.folder / 'updates'
            folder.mkdir(parents=True, exist_ok=True)
            archive = folder / info['_name']
            self._download(info['_zip'], archive)
            self.state['status'] = '파일 확인 중'
            with fetch(info['_sums']) as response:
                expected = parse_sums(response.read(100_000).decode('utf-8', 'replace')).get(info['_name'])
            digest = hashlib.sha256()
            with archive.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b''):
                    digest.update(chunk)
            digest = digest.hexdigest()
            if not expected or digest != expected:
                archive.unlink(missing_ok=True)
                raise AppError('받은 업데이트 파일이 릴리스와 달라요(SHA256 불일치). 다시 시도해 주세요.', 502)
            root, target, program = install_layout()
            # 업데이터는 설치 폴더 밖(임시 폴더)에서 돌아야 자기 파일도 바꿀 수 있다.
            runner = Path(tempfile.mkdtemp(prefix='mepiti-updater-')) / program.name
            shutil.copy2(program, runner)
            runner.chmod(0o755)
            command = [str(runner), '--zip', str(archive), '--root', str(root), '--pid', str(os.getpid()),
                       '--launch', str(target), '--log-dir', str(self.folder)]
            env = {k: v for k, v in os.environ.items() if not k.startswith(('_PYI', '_MEI'))}   # 패키지 앱 내부 변수는 넘기지 않는다
            if sys.platform == 'win32':
                subprocess.Popen(command, close_fds=True, env=env, creationflags=0x00000008 | 0x00000200)
            else:
                subprocess.Popen(command, start_new_session=True, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.state.update(status=f"메피티를 끄고 {info['latest']}(으)로 바꾼 뒤 다시 켭니다.", restarting=True)
            quit_app()
        except AppError as e:
            self.state.update(status=str(e), error=True)
        except Exception as e:                                         # 어떤 실패도 앱을 망가뜨리지 않는다
            self.state.update(status=f'업데이트하지 못했어요({e.__class__.__name__}). 릴리스 페이지에서 받아 설치해 주세요.', error=True)
        finally:
            self.state['running'] = False

    def _download(self, url, target):
        with fetch(url, timeout=60) as response, open(target, 'wb') as out:
            total = int(response.headers.get('Content-Length') or 0) or None
            self.state['total'] = total
            done = 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                self.state['completed'] = done
