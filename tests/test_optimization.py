"""전체 점검에서 확인한 동시 조회·캐시·입력 검증 회귀 사례."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from mepiti import conditions, earnings, goals, history, notices, peers, starforce, statcalc
from mepiti.chat import max_star
from mepiti.core import AppError, Store, now
from mepiti.server import Application, CachedNexon, make_server


class CachedNexonTests(unittest.TestCase):
    def test_simultaneous_requests_share_one_call_and_independent_results(self):
        entered, release = threading.Event(), threading.Event()
        barrier = threading.Barrier(8)
        calls = []

        class Nexon:
            def character(self, name, details=False):
                calls.append(name)
                entered.set()
                if not release.wait(3):
                    raise RuntimeError('test timed out')
                return {'name': name, 'equipment': [{'starforce': 18}]}

        cached = CachedNexon(Nexon())
        def get():
            barrier.wait(timeout=3)
            return cached.character('테스트', True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = [pool.submit(get) for _ in range(8)]
            self.assertTrue(entered.wait(3))
            release.set()
            results = [job.result(timeout=3) for job in jobs]
        self.assertEqual(calls, ['테스트'])
        results[0]['equipment'][0]['starforce'] = 99
        self.assertTrue(all(r['equipment'][0]['starforce'] == 18 for r in results[1:]))
        self.assertEqual(cached.character('테스트', True)['equipment'][0]['starforce'], 18)

    def test_cache_clear_during_request_does_not_restore_old_account(self):
        entered, release = threading.Event(), threading.Event()
        cached = CachedNexon(None)
        def old():
            entered.set()
            release.wait(3)
            return {'account': 'old'}
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(cached._cached, ('list',), old)
            self.assertTrue(entered.wait(3))
            cached.clear()
            self.assertEqual(cached._cached(('list',), lambda: {'account': 'new'}), {'account': 'new'})
            release.set()
            self.assertEqual(pending.result(timeout=3), {'account': 'old'})
        self.assertEqual(cached._cached(('list',), lambda: self.fail('cache miss')), {'account': 'new'})

    def test_failure_is_retryable_and_expiry_refreshes(self):
        cached = CachedNexon(None)
        with self.assertRaises(AppError):
            cached._cached(('list',), lambda: (_ for _ in ()).throw(AppError('실패')))
        self.assertFalse(cached.inflight)
        self.assertEqual(cached._cached(('list',), lambda: [1]), [1])
        cached.TTL = 0
        self.assertEqual(cached._cached(('list',), lambda: [2]), [2])

    def test_all_cache_kinds_are_bounded(self):
        cached = CachedNexon(None)
        cached.MAX_ENTRIES = 4
        for i in range(12):
            cached._cached(('union', i), lambda: [i])
        self.assertEqual(len(cached.cache), 4)


class StoreOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_character_fetch_uses_constant_connections_and_latest_two_snapshots(self):
        stamp = now()
        with self.store.db() as db:
            for i in range(20):
                db.execute('INSERT INTO characters VALUES(?,?,?,?,?,?)', (str(i), f'테스트{i}', '', 0, i == 0, stamp))
                for j in range(5):
                    db.execute('INSERT INTO snapshots VALUES(?,?,?,?)',
                               (f'{i}-{j}', str(i), json.dumps({'level': j}), stamp))
        with patch.object(self.store, 'db', wraps=self.store.db) as opened:
            characters = self.store.characters()
        self.assertLessEqual(opened.call_count, 2)
        for character in characters:
            self.assertEqual([s['data']['level'] for s in character['snapshots']], [4, 3])
            self.assertEqual(character['changes']['level'], 1)
        with patch.object(self.store, 'db', wraps=self.store.db) as opened:
            plain = self.store.characters(include_snapshots=False)
        self.assertEqual(len(plain), 20)
        self.assertEqual(opened.call_count, 1)
        self.assertNotIn('snapshots', plain[0])

    def test_legacy_earnings_migration_preserves_data_and_runs_once_concurrently(self):
        with self.store.db() as db:
            db.executescript(earnings.SCHEMA)
            db.execute("INSERT INTO earnings(id,kind,day,meso,created_at) VALUES('old','hunt','2026-09-01',123,?)", (now(),))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: earnings.ensure(self.store), range(8)))
        self.assertEqual(self.store.rows('SELECT meso,character,source_key FROM earnings'),
                         [{'meso': 123.0, 'character': None, 'source_key': None}])
        with patch.object(self.store, 'db', wraps=self.store.db) as opened:
            earnings.ensure(self.store)
        self.assertEqual(opened.call_count, 0)
        reopened = Store(self.folder.name)
        earnings.ensure(reopened)
        self.assertEqual(reopened.rows('SELECT meso FROM earnings')[0]['meso'], 123)

    def test_failed_schema_initialization_can_retry(self):
        with self.assertRaises(AppError):
            with self.store.schema('test') as needed:
                self.assertTrue(needed)
                raise AppError('실패')
        with self.store.schema('test') as needed:
            self.assertTrue(needed)

    def test_document_review_only_loads_requested_document(self):
        doc = self.store.import_document({'title': '시험', 'body': '시험용 본문',
                                         'metadata': {'source_url': 'https://example.com/test'}})
        with patch.object(self.store, 'documents', side_effect=AssertionError('전체 본문 조회')):
            self.store.review({'id': doc['id'], 'approve': False})
            self.assertEqual(self.store.document_counts(), (1, 0))


class CalculationValidationTests(unittest.TestCase):
    def test_nonfinite_and_fractional_inputs_are_user_errors(self):
        valid = {'level': 250, 'current_star': 18, 'target_star': 22, 'spare_cost': 1000}
        for key in ('level', 'current_star', 'target_star', 'spare_cost'):
            for value in ('NaN', 'Infinity', '-Infinity'):
                with self.subTest(key=key, value=value), self.assertRaises(AppError):
                    starforce.expected({**valid, key: value})
        with self.assertRaises(AppError):
            starforce.expected({**valid, 'target_star': 22.5})

    def test_duplicate_discount_does_not_reduce_cost_twice(self):
        valid = {'level': 250, 'current_star': 15, 'target_star': 17}
        once = starforce.expected({**valid, 'discounts': ['MVP 다이아']})
        twice = starforce.expected({**valid, 'discounts': ['MVP 다이아', 'MVP 다이아']})
        self.assertEqual(once, twice)
        with self.assertRaises(AppError):
            starforce.expected({**valid, 'discounts': ['MVP 골드', 'MVP 다이아']})
        with self.assertRaises(AppError):
            conditions.from_answer({'discounts': ['MVP 골드', 'MVP 다이아']})

    def test_solver_satisfies_each_original_equation(self):
        matrix = [[0, 2, 1], [3, -1, 2], [1, 1, 4]]
        vectors = [[5, 4, 6], [2, 1, 9], [-1, 0, 2]]
        original = [r[:] for r in matrix]
        for solved, vector in zip(starforce.solve_many(matrix, vectors), vectors):
            for row, rhs in zip(matrix, vector):
                self.assertAlmostEqual(sum(a * b for a, b in zip(row, solved)), rhs)
        self.assertEqual(matrix, original)

    def test_max_star_options_match_calculator_at_level_boundaries(self):
        for level, expected in ((94, 5), (95, 10), (107, 10), (108, 15), (117, 15),
                                (118, 15), (127, 15), (128, 20), (137, 20), (138, 30)):
            self.assertEqual(max_star({'equip_level': level}), expected)


class KeyCacheTests(unittest.TestCase):
    def test_account_discovery_cannot_save_previous_key_listing(self):
        entered, release = threading.Event(), threading.Event()
        class Nexon:
            def characters(self):
                entered.set()
                release.wait(3)
                return {'characters': [{'name': '이전계정'}]}
        with tempfile.TemporaryDirectory() as folder:
            app = Application(folder)
            app.nexon = CachedNexon(Nexon())
            with ThreadPoolExecutor(max_workers=1) as pool:
                job = pool.submit(app.route, 'POST', '/api/characters/discover', {}, {})
                self.assertTrue(entered.wait(3))
                app.forget_nexon_cache()
                release.set()
                with self.assertRaises(AppError) as error:
                    job.result(timeout=3)
                self.assertEqual(error.exception.status, 409)
            self.assertEqual(app.store.setting(earnings.ACCOUNT_CHARACTERS), [])

    def test_old_key_check_cannot_prime_new_key_cache(self):
        class Vault:
            def get(self):
                return 'old-key'
        entered, release = threading.Event(), threading.Event()
        def old_listing():
            entered.set()
            release.wait(3)
            return {'characters': [{'name': '이전계정'}]}
        with tempfile.TemporaryDirectory() as folder:
            app = Application(folder)
            app.vault = Vault()
            with patch.object(app, 'nexon_for') as factory, ThreadPoolExecutor(max_workers=1) as pool:
                factory.return_value.characters.side_effect = old_listing
                job = pool.submit(app.check_key)
                self.assertTrue(entered.wait(3))
                app.forget_nexon_cache()
                new = {'characters': [{'name': '새계정'}]}
                app.nexon.prime(('list',), new)
                release.set()
                job.result(timeout=3)
            self.assertEqual(app.nexon.characters(), new)

    def test_deleting_key_clears_account_derived_caches(self):
        class Vault:
            def delete(self):
                pass
        with tempfile.TemporaryDirectory() as folder:
            app = Application(folder)
            app.vault = Vault()
            app.nexon.prime(('list',), {'characters': [{'name': '이전계정'}]})
            app.store.set_setting(earnings.ACCOUNT_CHARACTERS, [{'name': '이전계정'}])
            app.store.set_setting(goals.EXP_DAYS, {'이전계정': {}})
            app.store.set_setting(history.FETCHED, ['2026-09-01'])
            app.route('POST', '/api/settings/key/delete', {}, {})
            self.assertFalse(app.nexon.cache)
            self.assertEqual(app.store.setting(earnings.ACCOUNT_CHARACTERS), [])
            self.assertEqual(app.store.setting(goals.EXP_DAYS), {})
            self.assertEqual(app.store.setting(history.FETCHED), [])

    def test_successful_key_check_reuses_verified_listing(self):
        class Vault:
            def get(self):
                return 'test-key'
        with tempfile.TemporaryDirectory() as folder:
            app = Application(folder)
            app.vault = Vault()
            listing = {'characters': [{'name': '테스트'}]}
            with patch.object(app, 'nexon_for') as factory:
                factory.return_value.characters.return_value = listing
                self.assertEqual(app.check_key(), {'state': 'ok'})
            with patch.object(app.nexon.nexon, 'characters', side_effect=AssertionError('중복 호출')):
                self.assertEqual(app.nexon.characters(), listing)


class ExternalOperationTests(unittest.TestCase):
    def test_stat_source_collection_is_shared_for_same_character(self):
        entered, release = threading.Event(), threading.Event()
        def fetch(*args):
            entered.set()
            release.wait(3)
            return {'sample': 1}
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            with patch.object(statcalc, 'fetch', side_effect=fetch) as called, \
                    patch.object(statcalc, 'build', side_effect=lambda raw, tables: raw), \
                    ThreadPoolExecutor(max_workers=6) as pool:
                jobs = [pool.submit(statcalc.load, store, None, '테스트') for _ in range(6)]
                self.assertTrue(entered.wait(3))
                release.set()
                self.assertTrue(all(job.result(timeout=3) == {'sample': 1} for job in jobs))
            self.assertEqual(called.call_count, 1)

    def test_parallel_notice_sync_does_not_repeat_collection(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        class Nexon:
            def notices(self, kind):
                calls.append(kind)
                entered.set()
                release.wait(3)
                return []
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            with ThreadPoolExecutor(max_workers=6) as pool:
                jobs = [pool.submit(notices.sync, store, Nexon()) for _ in range(6)]
                self.assertTrue(entered.wait(3))
                release.set()
                results = [job.result(timeout=3) for job in jobs]
            self.assertEqual(len(calls), 3)
            self.assertEqual(sum(bool(r.get('skipped')) for r in results), 5)

    def test_parallel_peer_requests_cannot_exceed_last_daily_call(self):
        calls = []
        class Nexon:
            def get(self, path, query):
                calls.append(path)
                return {}
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            store.set_setting(peers.DAILY_SETTING, 1)
            runner = peers.Peers(store, lambda: Nexon(), sleep=lambda _: None)
            def get(_):
                try:
                    runner.get('id', {})
                    return True
                except AppError as e:
                    self.assertEqual(e.status, 429)
                    return False
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(get, range(6)))
            self.assertEqual(sum(results), 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(runner.calls()['count'], 1)


class StaticAssetTests(unittest.TestCase):
    def test_png_assets_are_served_and_unknown_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Application(folder)
            server = make_server(app, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                for filename in ('icon.png', 'sol-erda-piece.png'):
                    with urlopen(base + '/' + filename, timeout=3) as response:
                        self.assertEqual(response.headers['Content-Type'], 'image/png')
                        self.assertTrue(response.read().startswith(b'\x89PNG'))
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + '/pyproject.toml', timeout=3)
                self.assertEqual(error.exception.code, 404)
                req = Request(base + '/api/starforce', data=b'{"level":250,"current_star":18,"target_star":22,"spare_cost":"Infinity"}',
                              headers={'Content-Type': 'application/json', 'X-Mepiti-Token': app.token})
                with self.assertRaises(HTTPError) as error:
                    urlopen(req, timeout=3)
                self.assertEqual(error.exception.code, 400)
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()
