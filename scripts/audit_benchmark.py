"""임시 DB·가상 자료로 최적화 전후를 비교한다. 실제 키·사용자 DB는 쓰지 않는다.

실행: .venv/bin/python -m scripts.audit_benchmark --output /tmp/magpt-benchmark.json
시간은 같은 기기의 중앙값이며 게임 수치의 정확도나 외부 API 속도를 뜻하지 않는다.
"""
import argparse
import hashlib
import json
import statistics
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from mepiti import earnings, history, starforce
from mepiti.core import Store


def measure(call, repeats=5):
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        result = call()
        times.append((time.perf_counter() - start) * 1000)
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
    return {'median_ms': round(statistics.median(times), 3),
            'result_sha256': hashlib.sha256(serialized).hexdigest()}


def run():
    with tempfile.TemporaryDirectory(prefix='mepiti-audit-') as folder:
        store = Store(folder)
        earnings.ensure(store)
        today = earnings.today()
        stamp = today.isoformat() + 'T00:00:00+09:00'
        with store.db() as db:
            db.executemany('INSERT INTO characters VALUES(?,?,?,?,?,?)',
                           [(str(i), f'테스트{i}', '', 0, int(i == 0), stamp) for i in range(79)])
            db.executemany('INSERT INTO snapshots VALUES(?,?,?,?)',
                           [(f'{i}-{j}', str(i), json.dumps({'level': 200 + j, 'combat_power': j * 1000}), stamp)
                            for i in range(79) for j in range(30)])
            db.executemany('INSERT INTO earnings(id,kind,day,meso,pieces,piece_price,flasks,crystal,party,extra,created_at,character) '
                           'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           [(str(i), 'boss' if i % 3 == 0 else 'hunt',
                             (today - timedelta(days=i % 730)).isoformat(), 1000, 3, 10000, 1, 6000, 2, 500,
                             stamp, f'테스트{i % 79}') for i in range(10000)])
        picked = {'discounts': ['MVP 다이아']}
        inputs = {'level': 250, 'current_star': 18, 'target_star': 22, 'spare_cost': 10000000, 'event': '샤타포스'}
        return {'scope': {'characters': 79, 'snapshots': 2370, 'earnings': 10000, 'repeats': 5},
                'characters': measure(store.characters),
                'earnings': measure(lambda: earnings.overview(store)),
                'history_costs_10000': measure(lambda: sum(history.attempt_cost(250, i % 30, False, picked)
                                                         for i in range(10000))),
                'starforce_100': measure(lambda: [starforce.expected(inputs) for _ in range(100)])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run()
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
