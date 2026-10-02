"""임시 저장소의 근거 검색·시세 경합 회귀 검사. 외부 API는 호출하지 않는다."""
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch

from mepiti import prices
from mepiti.core import KST, Store


class StorageAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        prices.register_fetcher(None)

    def tearDown(self):
        prices.register_fetcher(None)
        self.tmp.cleanup()

    def seed_docs(self, count=1):
        current = datetime.now(KST)
        meta = {'verification_status': 'reviewed', 'region': 'KR', 'server_type': 'live',
                'effective_from': (current - timedelta(days=1)).isoformat(),
                'valid_until': (current + timedelta(days=1)).isoformat(), 'topic': '시험', 'version': 'v1'}
        with self.store.db() as db:
            db.executemany('INSERT INTO documents VALUES(?,?,?,?)',
                           [(str(i), '시험 자료', '기대값 검증\n 조건 A\n\n조건 B', json.dumps(meta)) for i in range(count)])
        return meta

    def test_search_large_eligible_set_keeps_latest_ties_and_passages(self):
        self.seed_docs(1001)
        result, conflict = self.store.search('기댓값')
        self.assertFalse(conflict)
        self.assertEqual([d['id'] for d in result], ['1000', '999', '998', '997', '996'])
        self.assertEqual(result[0]['score'], 1)
        self.assertEqual(result[0]['passages'], ['기대값 검증', '조건 A', '조건 B'])

    def test_conflict_outside_top_five_still_detected(self):
        meta = self.seed_docs(6)
        meta['version'] = 'v2'
        with self.store.db() as db:
            db.execute('UPDATE documents SET metadata=? WHERE id=?', (json.dumps(meta), '0'))
        docs, conflict = self.store.search('기대값')
        self.assertEqual(len(docs), 5)
        self.assertTrue(conflict)

    def test_search_filters_dates_timezones_scope_and_review_changes(self):
        meta = self.seed_docs(7)
        current = datetime.now(KST)
        excluded = [{'verification_status': 'unreviewed'}, {'region': 'other'}, {'server_type': 'test'},
                    {'effective_from': (current + timedelta(days=1)).isoformat()},
                    {'valid_until': (current - timedelta(seconds=1)).isoformat()},
                    {'effective_to': (current - timedelta(seconds=1)).isoformat()}]
        with self.store.db() as db:
            for i, changes in enumerate(excluded):
                db.execute('UPDATE documents SET metadata=? WHERE id=?', (json.dumps({**meta, **changes}), str(i)))
        self.assertEqual([d['id'] for d in self.store.search('기대값')[0]], ['6'])
        self.store.review({'id': '6', 'approve': False})
        self.assertEqual(self.store.search('기대값'), ([], False))
        self.store.review({'id': '6', 'approve': True})
        self.assertEqual(self.store.search('!')[0], [])
        self.assertEqual(len(self.store.search('기대값')[0]), 1)

    def test_same_second_price_uses_latest_insert_with_and_without_grade(self):
        with patch('mepiti.core.now', return_value='2026-10-03T12:00:00+09:00'):
            for grade, amount in [(98, 10), (98, 20), (100, 30)]:
                self.store.price_save({'item': '시험 모자', 'add_grade': grade, 'price': amount})
        self.assertEqual(self.store.price_lookup('시험 모자', 98)['price'], 20)
        self.assertEqual(self.store.price_lookup('시험 모자')['price'], 30)
        self.assertEqual(self.store.price_lookup('시험 모자', 101)['price'], 30)
        self.assertEqual([p['price'] for p in self.store.prices()], [30, 20, 10])

    def test_daily_count_not_hidden_by_more_than_1000_user_prices(self):
        stamp = '2026-10-03T12:00:00+09:00'
        with self.store.db() as db:
            db.executemany('INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)',
                           [(str(i), '시험', None, None, 1, 'user' if i else 'auction', None, stamp if not i else '2026-10-03T13:00:00+09:00')
                            for i in range(1101)])
        with patch('mepiti.prices.now', return_value=stamp):
            self.assertEqual(prices._used_today(self.store), 1)

    def test_daily_count_excludes_other_days_and_includes_all_fetch_sources(self):
        with self.store.db() as db:
            db.executemany('INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)',
                           [(str(i), '시험', None, None, 1, source, None, stamp)
                            for i, (source, stamp) in enumerate([
                                ('auction', '2026-10-02T23:59:59+09:00'), ('auction', '2026-10-03T00:00:00+09:00'),
                                ('test', '2026-10-03T23:59:59+09:00'), ('user', '2026-10-03T12:00:00+09:00'),
                                ('auction', '2026-10-04T00:00:00+09:00')])])
        self.assertEqual(self.store.price_fetch_count('2026-10-03'), 2)

    def test_duplicate_concurrent_price_queries_fetch_once(self):
        calls = []
        entered, release = threading.Event(), threading.Event()
        def fetch(item, grade):
            calls.append(item); entered.set()
            self.assertTrue(release.wait(3))
            return {'price': 10}
        prices.register_fetcher(fetch)
        self.store.set_setting(prices.FETCH_SETTING, '1')
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(prices.resolve, self.store, '시험 모자') for _ in range(6)]
            self.assertTrue(entered.wait(3)); time.sleep(.05); release.set()
            result = [f.result(timeout=3) for f in futures]
        self.assertEqual(calls, ['시험 모자'])
        self.assertTrue(all(r['price'] == 10 for r in result))
        self.assertEqual(self.store.price_count(), 1)
        self.assertEqual(self.store._operations, {})

    def test_concurrent_different_items_respect_last_budget_slot(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def fetch(item, grade):
            calls.append(item); entered.set()
            self.assertTrue(release.wait(3))
            return {'price': 10}
        prices.register_fetcher(fetch)
        self.store.set_setting(prices.FETCH_SETTING, '1')
        self.store.set_setting(prices.DAILY_LIMIT_SETTING, '1')
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(prices.resolve, self.store, '시험 모자')
            self.assertTrue(entered.wait(3))
            second = pool.submit(prices.resolve, self.store, '시험 벨트')
            time.sleep(.05); release.set()
            self.assertTrue(first.result(timeout=3)['known'])
            self.assertEqual(second.result(timeout=3)['reason'], 'limit')
        self.assertEqual(calls, ['시험 모자'])

    def test_saved_price_does_not_wait_for_unrelated_external_fetch(self):
        self.store.price_save({'item': '저장된 시험 장비', 'price': 20})
        self.store.set_setting(prices.FETCH_SETTING, '1')
        entered, release = threading.Event(), threading.Event()
        def fetch(item, grade):
            entered.set()
            self.assertTrue(release.wait(3))
            return {'price': 10}
        prices.register_fetcher(fetch)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(prices.resolve, self.store, '없는 시험 장비')
            try:
                self.assertTrue(entered.wait(3))
                cached = pool.submit(prices.resolve, self.store, '저장된 시험 장비')
                self.assertEqual(cached.result(timeout=1)['price'], 20)
            finally:
                release.set()
            self.assertTrue(pending.result(timeout=3)['known'])

    def test_failed_price_query_releases_lock_for_retry(self):
        self.store.set_setting(prices.FETCH_SETTING, '1')
        prices.register_fetcher(lambda item, grade: (_ for _ in ()).throw(RuntimeError('offline')))
        self.assertFalse(prices.resolve(self.store, '시험')['known'])
        prices.register_fetcher(lambda item, grade: {'price': 10})
        self.assertTrue(prices.resolve(self.store, '시험')['known'])
        self.assertEqual(self.store._operations, {})
