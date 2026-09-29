"""업데이터(MepitiUpdater) — 메피티가 꺼지길 기다렸다가 새 앱 파일로 바꾸고 다시 켠다.

메피티 본체와 따로 빌드되는 작은 실행 파일이다(scripts/updater_entry.py). 표준 라이브러리만 쓴다.
본체는 업데이트 파일을 받아 검증한 뒤, 이 프로그램을 임시 폴더로 복사해 실행하고 스스로 종료한다.
(설치 폴더 안에서 실행하면 자기 파일을 바꿀 수 없다.)

바꾸는 방식: 업데이트 zip의 맨 위 항목(Windows: Mepiti.exe·_internal·MepitiUpdater.exe, macOS: Mepiti.app)마다
기존 것을 '이름.old'로 옮기고 새 것을 넣는다. 하나라도 실패하면 전부 되돌리고 원래 앱을 다시 켠다.
Windows 설치 폴더의 제거 프로그램(unins000.exe)처럼 zip에 없는 파일은 건드리지 않는다.
데이터(~/.mepiti)와 API 키(OS 보안 저장소)는 설치 폴더 밖이라 바꾸지 않는다.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

STAGING = '.mepiti-update'


def log(folder, text):
    try:
        with open(Path(folder) / 'update.log', 'a', encoding='utf-8') as out:
            out.write(f"{datetime.now().isoformat(timespec='seconds')} {text}\n")
    except OSError:
        pass


def wait_exit(pid, timeout=90):
    """메피티(pid)가 끝날 때까지 기다린다. 이미 없으면 바로 True."""
    if pid <= 0:
        return True
    if sys.platform == 'win32':
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x00100000, False, pid)            # SYNCHRONIZE
        if not handle:
            return True
        try:
            return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel.CloseHandle(handle)
    end = time.time() + timeout
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.3)
    return False


def remove(path):
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except OSError:
            pass


def retry(action, tries=20, pause=0.5):
    """Windows는 방금 끝난 프로세스가 파일을 잠깐 잡고 있을 수 있어 몇 번 다시 해 본다."""
    for attempt in range(tries):
        try:
            return action()
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(pause)


def extract(archive, staging):
    remove(staging)
    staging.mkdir(parents=True)
    if sys.platform == 'darwin':
        # macOS 앱은 실행 권한·심볼릭 링크·서명이 그대로 있어야 한다. 파이썬 zipfile은 이것을 잃는다.
        subprocess.run(['ditto', '-x', '-k', str(archive), str(staging)], check=True, capture_output=True)
    else:
        with zipfile.ZipFile(archive) as z:
            z.extractall(staging)
    entries = [p for p in staging.iterdir() if p.name != '__MACOSX']
    if not entries:
        raise RuntimeError('업데이트 파일이 비어 있습니다.')
    return entries


def swap(root, entries):
    """새 항목으로 바꾼다. 실패하면 이미 바꾼 것까지 되돌리고 예외를 다시 던진다. 남은 .old 경로 목록을 돌려준다."""
    done = []
    try:
        for new in entries:
            dest, old = root / new.name, root / (new.name + '.old')
            remove(old)
            if dest.exists() or dest.is_symlink():
                retry(lambda: os.replace(dest, old))
            done.append((dest, old))
            retry(lambda: shutil.move(str(new), str(dest)))
    except Exception:
        for dest, old in reversed(done):
            remove(dest)
            if old.exists():
                os.replace(old, dest)
        raise
    return [old for _, old in done]


def clean_env():
    """PyInstaller로 만든 프로그램끼리 서로 띄울 때 남는 내부 환경 변수를 뺀다(다른 프로그램의 실행 폴더를 가리킨다)."""
    return {k: v for k, v in os.environ.items() if not k.startswith(('_PYI', '_MEI'))}


def launch(target):
    target = Path(target)
    if sys.platform == 'darwin':
        subprocess.Popen(['open', str(target)])
    elif sys.platform == 'win32':
        subprocess.Popen([str(target)], cwd=str(target.parent), close_fds=True, env=clean_env(),
                         creationflags=0x00000008 | 0x00000200)          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        subprocess.Popen([str(target)], start_new_session=True)


def apply(archive, root, pid, target, log_dir, relaunch=True):
    """업데이트를 적용한다. 성공하면 0, 실패하면 1(원래 앱을 다시 켠다)."""
    root, archive = Path(root), Path(archive)
    log(log_dir, f'시작: {archive.name} → {root}')
    if not wait_exit(pid):
        log(log_dir, '메피티가 끝나지 않아 업데이트하지 않았습니다.')
        return 1
    time.sleep(0.5)
    staging = root / STAGING
    try:
        olds = swap(root, extract(archive, staging))
    except Exception as e:                                               # 되돌린 뒤 원래 앱을 켠다
        log(log_dir, f'실패, 되돌림: {e!r}')
        remove(staging)
        if relaunch:
            launch(target)
        return 1
    remove(staging)
    for old in olds:
        remove(old)
    remove(archive)
    log(log_dir, '완료')
    if relaunch:
        launch(target)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='메피티 업데이터')
    parser.add_argument('--zip', required=True)
    parser.add_argument('--root', required=True, help='업데이트 zip의 맨 위 항목이 들어갈 폴더')
    parser.add_argument('--pid', type=int, default=0, help='끝나길 기다릴 메피티 프로세스')
    parser.add_argument('--launch', required=True, help='업데이트 뒤 다시 켤 앱')
    parser.add_argument('--log-dir', default=str(Path.home() / '.mepiti'))
    args = parser.parse_args(argv)
    return apply(args.zip, args.root, args.pid, args.launch, args.log_dir)


if __name__ == '__main__':
    raise SystemExit(main())
