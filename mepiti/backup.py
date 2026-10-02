"""데이터 파일 백업. 앱을 새 버전으로 바꿔 처음 켤 때 한 번, 그리고 설정에서 누를 때.

데이터(수익·캐릭터·노작값·대화·설정)는 앱 설치 폴더가 아니라 사용자 폴더(~/.mepiti/mepiti.sqlite3)에 있어서
앱을 바꿔 깔아도 지워지지 않는다. 그래도 새 버전이 표를 고치다 문제가 생길 때를 대비해, 버전이 바뀌면
표를 건드리기 전에 통째로 복사해 둔다. API 키는 이 파일에 없고 OS 보안 저장소에 따로 있다(백업하지 않는다).
"""
import json
import re
import sqlite3
import tempfile
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .core import AppError, KST

FOLDER = 'backups'
KEEP = 5                      # 오래된 것부터 지운다
VERSION_SETTING = 'app_version'
DATA_FILE = 'mepiti.sqlite3'
_LOCK = threading.RLock()       # 같은 프로세스에서 백업 중인 파일을 정리/목록 조회하지 않는다.


def data_file(folder):
    return Path(folder) / DATA_FILE


def last_version(folder):
    """지난번에 이 데이터를 연 앱 버전. 파일이 없거나 기록이 없으면 None."""
    path = data_file(folder)
    if not path.exists():
        return None
    try:
        # sqlite3 연결의 with는 커밋만 하고 닫지 않는다. Windows에서 파일이 잠겨 남지 않게 closing으로 닫는다.
        with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)) as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (VERSION_SETTING,)).fetchone()
        return json.loads(row[0]) if row else None
    except (sqlite3.Error, ValueError):
        return None


def make(folder, label):
    """지금 데이터를 백업 폴더에 복사한다. 쓰는 중이어도 안전하게 sqlite의 백업 기능으로 뜬다."""
    with _LOCK:
        return _make(folder, label)


def _make(folder, label):
    source = data_file(folder)
    if not source.exists():
        raise AppError('아직 백업할 데이터가 없습니다.', 404)
    target_dir = Path(folder) / FOLDER
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(KST).strftime('%Y%m%d-%H%M%S')
    safe = re.sub(r'[^0-9A-Za-z._-]+', '-', label).strip('-') or 'manual'
    # 같은 초의 연속/동시 백업도 기존 백업을 덮어쓰지 않는다. 생성 권한은 0600이다.
    with tempfile.NamedTemporaryFile(prefix=f'mepiti-{stamp}-{safe}-', suffix='.sqlite3', dir=target_dir, delete=False) as f:
        target = Path(f.name)
    try:
        with closing(sqlite3.connect(str(source), timeout=15)) as src, closing(sqlite3.connect(str(target))) as dst:
            src.backup(dst)
    except (sqlite3.Error, OSError):
        target.unlink(missing_ok=True)
        raise
    prune(folder)
    return target


def prune(folder, keep=KEEP):
    with _LOCK:
        for old in backup_files(folder)[keep:]:
            old.unlink(missing_ok=True)


def listing(folder):
    with _LOCK:
        return [{'name': f.name, 'size_kb': round(f.stat().st_size / 1024, 1),
                 'created': datetime.fromtimestamp(f.stat().st_mtime, KST).isoformat(timespec='seconds')}
                for f in backup_files(folder)]


def backup_files(folder):
    return sorted((Path(folder) / FOLDER).glob('mepiti-*.sqlite3'),
                  key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)


def on_start(folder, version):
    """앱을 켤 때 부른다(표를 고치기 전). 지난번과 버전이 다르면 백업한다. 만든 백업 경로 또는 None."""
    before = last_version(folder)
    if not data_file(folder).exists() or before == version:
        return None
    try:
        return make(folder, f'v{before}-to-v{version}' if before else f'before-v{version}')
    except (sqlite3.Error, OSError):
        return None           # 백업 실패로 앱이 안 켜지면 안 된다. 원본은 그대로 있다.
