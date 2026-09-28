"""사용자가 고를 수 있는 로컬 모델과, 기기에 맞는 추천.

선택지는 두 개다. 2026-09-28 사용자 Mac(M4/16GB)에서 scripts/eval_models.py로 비교한 결과
(HANDOFF.md '가벼운 모델 찾기')로 골랐다. 기본값을 앱이 정하지 않고 설치 마법사나 첫 실행에서 사용자가 고른다.

메모리는 Ollama에 올렸을 때 실측값(num_ctx 8192)이다. 게임과 함께 돌리면 게임도 VRAM을 쓰므로
VRAM 4GB(GTX 1650 등)에서는 2B가 현실적이다.
"""
import json
import re
from pathlib import Path

PRESETS = [
    {'id': 'light', 'model': 'qwen3.5:2b', 'label': '2B · 가벼움',
     'download_gb': 2.7, 'memory_gb': 2.4, 'license': 'Apache 2.0',
     'fits': 'VRAM 4GB(GTX 1650 등)에서 게임과 함께',
     'note': '빠르다. 앱이 보여 준 수치를 부정하지 않는다. 서술은 짧고 얕은 편.'},
    {'id': 'quality', 'model': 'exaone3.5:7.8b', 'label': '8B · 품질',
     'download_gb': 4.8, 'memory_gb': 5.2, 'license': 'EXAONE NC (비상업)',
     'fits': 'VRAM 8GB 이상, 또는 Apple Silicon 메모리 16GB 이상',
     'note': '서술이 더 매끄럽다. 2B보다 3~4배 느리고, VRAM이 모자라면 CPU로 넘어가 더 느려진다.'},
]
SETUP_FILE = 'setup.json'      # 설치 마법사가 남기는 선택. 첫 실행에서 읽고 지운다.


def by_id(preset_id):
    return next((p for p in PRESETS if p['id'] == preset_id), None)


def vram_gb(gpu_text):
    """nvidia-smi 출력('NVIDIA GeForce GTX 1650, 4096 MiB')에서 VRAM을 GB로."""
    found = re.search(r'(\d+)\s*MiB', gpu_text or '')
    return round(int(found.group(1)) / 1024, 1) if found else None


def recommend(system):
    """기기 사양으로 추천. 판단할 수 없으면 가벼운 쪽을 권한다."""
    vram = vram_gb((system or {}).get('gpu'))
    if vram is not None:
        return 'quality' if vram >= 7.5 else 'light'
    apple = (system or {}).get('os') == 'Darwin' and (system or {}).get('architecture') == 'arm64'
    ram = (system or {}).get('ram_gb') or 0
    if apple and ram >= 16:
        return 'quality'
    return 'light'


def describe(system, installed, selected):
    """화면에 보여 줄 선택지. 설치·사용 상태와 추천을 붙인다."""
    best = recommend(system)
    return [{**p, 'installed': p['model'] in installed, 'selected': p['model'] == selected,
             'recommended': p['id'] == best} for p in PRESETS]


def setup_choice(folder):
    """설치 마법사에서 고른 선택지. 없거나 '나중에'면 None."""
    path = Path(folder) / SETUP_FILE
    try:
        choice = json.loads(path.read_text(encoding='utf-8')).get('model_choice')
    except (OSError, ValueError, AttributeError):
        return None
    return choice if by_id(choice) else None


def clear_setup(folder):
    try:
        (Path(folder) / SETUP_FILE).unlink()
    except OSError:
        pass
