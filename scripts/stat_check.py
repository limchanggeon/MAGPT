"""현재 스탯 재현 점검 — 캐릭터 하나의 스탯 출처를 넥슨 Open API로 모아 스탯창 값과 비교한다(mepiti/statcalc.py).

호출 약 22회(기본 10 + 스킬 차수 10 + 식별자 등). 결과는 화면에만 찍고 저장하지 않는다.
  .venv/bin/python scripts/stat_check.py 캐릭터이름
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mepiti import statcalc  # noqa: E402
from mepiti.adapters import Nexon, Vault  # noqa: E402


def fetch(nexon, name):
    ocid = nexon.get('id', {'character_name': name})['ocid']
    raw = {}
    for path in statcalc.PATHS:
        time.sleep(0.3)
        raw[path] = nexon.get(path, {'ocid': ocid})
    raw['skills'] = []
    for grade in statcalc.SKILL_GRADES:
        time.sleep(0.3)
        try:
            raw['skills'] += nexon.get('character/skill', {'ocid': ocid, 'character_skill_grade': grade}).get('character_skill') or []
        except Exception:
            pass
    return raw


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    ledger = statcalc.build(fetch(Nexon(Vault()), sys.argv[1]))
    report = statcalc.reproduce(ledger)
    print(f"주스탯 {report['main']} · 무기 상수 {report['weapon_constant']} · 숙련도 {report['mastery']}")
    for row in report['rows']:
        print(f"{row['stat']:>4}: 스탯창 {row['actual']:>7,} / 출처 합산 {row['predicted']:>7,} / 차이 {row['gap']:>6,} ({row['gap_pct']}%)"
              f"  [% 적용 고정치 {row['applied']:,} × (1 + {row['pct']}%) + % 미적용 {row['unapplied']:,}]")


if __name__ == '__main__':
    main()
