"""Import reviewed-source candidates from an explicit JSONL schema, never auto-approve."""
import argparse
import json
from pathlib import Path
from mepiti.core import Store, AppError

parser=argparse.ArgumentParser()
parser.add_argument('jsonl',type=Path)
parser.add_argument('--data-dir',default=str(Path.home()/'.mepiti'))
args=parser.parse_args()
store=Store(args.data_dir)
count=0
for line_number,line in enumerate(args.jsonl.read_text(encoding='utf-8-sig').splitlines(),1):
    if not line.strip():continue
    try:
        store.import_document(json.loads(line));count+=1
    except (AppError,ValueError) as e:
        raise SystemExit(f'{line_number}행 오류: {e}. 이전 {count}개는 검토 대기로 저장되었습니다.')
print(f'{count}개 자료를 검토 대기로 저장했습니다. 현재 화면에는 검토 메뉴가 없어 개발 도구로 원문과 적용 조건을 검토·승인해야 합니다.')
