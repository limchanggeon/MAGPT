"""두 번째 전체 점검: 호출 대기·프리셋·수집 재시도·목표·이미지·백업 경계 검증."""
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from mepiti import adapters, backup, earnings, goals, history, peers, server, statcalc
from mepiti.core import AppError, Store


def equipment(strength=100):
    return {'item_name': '시험 모자', 'item_equipment_slot': '모자', 'starforce': '18',
            'item_total_option': {'str': str(strength), 'attack_power': '10'},
            'potential_option_1': 'STR +10%'}


def ledger_raw(items):
    final = {'STR': 10000, 'DEX': 1000, '공격력': 1000, 'AP 배분 STR': 1000,
             '데미지': 50, '최종 데미지': 20, '보스 몬스터 데미지': 200,
             '방어율 무시': 95, '크리티컬 데미지': 70}
    return {'character/basic': {'character_level': 280},
            'character/stat': {'final_stat': [{'stat_name': k, 'stat_value': str(v)} for k, v in final.items()]},
            'character/item-equipment': {'item_equipment': items}, 'character/set-effect': {},
            'character/symbol-equipment': {}, 'character/hyper-stat': {}, 'user/union-raider': {},
            'user/union-artifact': {}, 'user/union-champion': {}, 'character/pet-equipment': {}, 'skills': []}


class GateFollowupTests(unittest.TestCase):
    def test_pause_extends_already_waiting_request(self):
        clock = SimpleNamespace(t=0.0)
        delays = []
        def sleep(delay):
            delays.append(delay)
            if len(delays) == 1:
                gate.pause(2)
            clock.t += delay
        gate = adapters.Gate(0.25, lambda: clock.t, sleep)
        gate.wait()
        gate.wait()
        self.assertGreaterEqual(clock.t, 2)

    def test_late_wakeup_does_not_allow_next_call_at_same_time(self):
        clock = SimpleNamespace(t=0.0)
        def sleep(delay):
            clock.t += delay + (1 if clock.t == 0 else 0)
        gate = adapters.Gate(0.25, lambda: clock.t, sleep)
        gate.wait()
        gate.wait()
        second = clock.t
        gate.wait()
        self.assertGreaterEqual(clock.t - second, 0.25)


class PresetFollowupTests(unittest.TestCase):
    def test_same_name_and_stars_different_options_are_compared(self):
        current = equipment()
        for change in ({'item_total_option': {'str': '500', 'attack_power': '100'}},
                       {'soul_option': '공격력 +3%'}, {'additional_potential_option_1': 'STR +13%'}):
            with self.subTest(change=change):
                stronger = {**current, **change}
                raw = ledger_raw([current])
                raw['character/item-equipment'].update({'preset_no': 1, 'item_equipment_preset_1': [current],
                                                        'item_equipment_preset_2': [stronger]})
                ledger = statcalc.build(raw)
                self.assertEqual(ledger.preset, 2)
                self.assertGreater(ledger.preset_scores[2], 0)
                self.assertEqual(ledger.items, [stronger])

    def test_ledger_totals_match_source_rows_and_remain_current(self):
        ledger = statcalc.Ledger()
        for i in range(50):
            ledger.add('ALL', '장비', str(i), i + 1)
            ledger.add('STR', '심볼', str(i), i + 1)
            ledger.add('ATT', '잠재', str(i), i % 3, 'pct')
        for classify in (None, {'장비': False, '심볼': True}, {'잠재': False}):
            for stat in ('STR', 'INT', 'ATT'):
                for kind in ('flat', 'pct'):
                    for applied in (None, True, False):
                        rules = classify or statcalc.CLASSIFY
                        expected = sum(v for source, _, v, k in ledger.rows.get(stat, [])
                                       if k == kind and (applied is None or rules.get(source, True) == applied))
                        self.assertEqual(ledger.total(stat, kind, classify, applied), expected)
        ledger.add('STR', '장비', '추가', 7)
        self.assertEqual(ledger.total('STR', 'flat'), 2557)


class DataFollowupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_future_income_does_not_count_as_earned(self):
        with patch.object(earnings, 'today', return_value=date(2026, 10, 2)):
            earnings.add(self.store, {'kind': 'hunt', 'day': '2026-10-03', 'meso': '1억', 'flasks': 2})
            plan = goals.meso_plan(self.store, '10억')
        self.assertEqual(plan['data_days'], 0)
        self.assertEqual(plan['daily'], 0)
        self.assertIsNone(plan['eta'])

    def test_old_income_keeps_history_notes_without_loading_all_records(self):
        with patch.object(earnings, 'today', return_value=date(2026, 10, 2)):
            earnings.add(self.store, {'kind': 'hunt', 'day': '2025-01-01', 'meso': 1})
            plan = goals.meso_plan(self.store)
        self.assertEqual(plan['data_days'], 7)
        self.assertEqual(plan['daily'], 0)
        self.assertTrue(any('소재비' in note for note in plan['notes']))
        self.assertFalse(any('수익 기록이 없어' in note for note in plan['notes']))

    def test_extreme_goal_keeps_numeric_estimate_without_date_overflow(self):
        with patch.object(earnings, 'today', return_value=date(2026, 10, 2)):
            earnings.add(self.store, {'kind': 'hunt', 'meso': 1})
            plan = goals.meso_plan(self.store, earnings.MAX_MESO)
        self.assertEqual(plan['days'], earnings.MAX_MESO)
        self.assertIsNone(plan['eta'])
        self.assertTrue(any('너무 길어' in note for note in plan['notes']))

    def test_same_second_backups_are_independent(self):
        with patch.object(backup, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 2, 23, 59)
            self.store.set_setting('test_value', 1)
            first = backup.make(self.folder.name, 'manual')
            self.store.set_setting('test_value', 2)
            second = backup.make(self.folder.name, 'manual')
        self.assertNotEqual(first, second)
        read = Store.__new__(Store)
        read.path = first
        self.assertEqual(read.setting('test_value'), 1)
        read.path = second
        self.assertEqual(read.setting('test_value'), 2)

    def test_history_stops_after_upstream_key_or_rate_limit_error(self):
        for code in (401, 403, 429):
            with self.subTest(code=code):
                asked = []
                def fail(day):
                    asked.append(day)
                    error = AppError('외부 오류', 502)
                    error.upstream = code
                    raise error
                with patch.object(history, 'resolve_levels') as resolve:
                    result = history.fetch(self.store, SimpleNamespace(starforce_history=fail), 14)
                resolve.assert_not_called()
                self.assertEqual(len(asked), 1)
                self.assertEqual(len(result['failed']), 1)

    def test_concurrent_exp_characters_preserve_both_caches(self):
        barrier = threading.Barrier(2)
        seen = set()
        def basic(name, day=None):
            if name not in seen:
                seen.add(name)
                barrier.wait(timeout=3)
            return {'level': 280, 'exp': 100, 'rate': 1}
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(goals.exp_plan, self.store, SimpleNamespace(basic_on=basic), name)
                    for name in ('테스트A', '테스트B')]
            [job.result(timeout=3) for job in jobs]
        self.assertEqual(set(self.store.setting(goals.EXP_DAYS)), {'테스트A', '테스트B'})

    def test_exp_stops_on_rate_limit_and_keeps_completed_days(self):
        asked = []
        def basic(name, day=None):
            asked.append(day)
            if len(asked) == 3:
                error = AppError('한도 초과', 502)
                error.upstream = 429
                raise error
            return {'level': 280, 'exp': 100, 'rate': 1}
        with self.assertRaises(AppError):
            goals.exp_plan(self.store, SimpleNamespace(basic_on=basic), '테스트')
        self.assertEqual(len(asked), 3)
        self.assertEqual(len(self.store.setting(goals.EXP_DAYS)['테스트']), 2)

    def test_peer_transient_failure_preserves_candidate_and_retry_counts_once(self):
        failed = []
        def get(path, query):
            if path == 'id': return {'ocid': 'test-id'}
            if path == 'character/stat': return {'final_stat': [{'stat_name': '전투력', 'stat_value': '250000000'}]}
            if path == 'character/item-equipment':
                if not failed:
                    failed.append(True)
                    error = AppError('일시 오류', 503)
                    raise error
                return {'item_equipment': [equipment()]}
            self.fail(path)
        runner = peers.Peers(self.store, lambda: SimpleNamespace(get=get), sleep=lambda _: None)
        self.store.set_setting(peers.TARGET, {'job': '전사-히어로', 'cp': 250000000})
        self.store.set_setting(peers.QUEUE, [{'name': '테스트'}])
        with self.assertRaises(AppError): runner.step()
        self.assertEqual(self.store.setting(peers.QUEUE), [{'name': '테스트'}])
        self.assertTrue(runner.step())
        self.assertEqual(self.store.setting(peers.QUEUE), [])
        self.assertEqual(self.store.setting(peers.TARGET)['matched'], 1)
        self.assertEqual(self.store.rows('SELECT COUNT(*) AS n FROM peers')[0]['n'], 1)
        self.assertFalse(runner.finals)

    def test_simultaneous_backups_keep_five_complete_files(self):
        self.store.set_setting('test_value', 7)
        with ThreadPoolExecutor(max_workers=8) as pool:
            names = list(pool.map(lambda _: backup.make(self.folder.name, 'manual'), range(8)))
        self.assertEqual(len(set(names)), 8)
        listing = backup.listing(self.folder.name)
        self.assertEqual(len(listing), 5)
        reader = Store.__new__(Store)
        for entry in listing:
            reader.path = self.store.folder / backup.FOLDER / entry['name']
            self.assertEqual(reader.setting('test_value'), 7)

    def test_peer_missing_character_does_not_block_queue(self):
        def get(path, query):
            error = AppError('없음', 502)
            error.upstream = 404
            raise error
        runner = peers.Peers(self.store, lambda: SimpleNamespace(get=get), sleep=lambda _: None)
        self.store.set_setting(peers.TARGET, {'job': '전사-히어로', 'cp': 250000000})
        self.store.set_setting(peers.QUEUE, [{'name': '삭제됨'}, {'name': '다음'}])
        self.assertTrue(runner.step())
        self.assertEqual(self.store.setting(peers.QUEUE), [{'name': '다음'}])

    def test_peer_temp_stats_are_bounded(self):
        def get(path, query):
            return {'ocid': query['character_name']} if path == 'id' else {'final_stat': []}
        runner = peers.Peers(self.store, lambda: SimpleNamespace(get=get), sleep=lambda _: None)
        for i in range(40): runner.power(str(i))
        self.assertLessEqual(len(runner.finals), 32)


class ImageFollowupTests(unittest.TestCase):
    def test_invalid_port_is_a_user_error(self):
        with self.assertRaises(AppError) as error:
            server.nexon_image(SimpleNamespace(), 'https://open.api.nexon.com:invalid/static/maplestory/a.png')
        self.assertEqual(error.exception.status, 400)

    def test_simultaneous_downloads_are_shared(self):
        entered, release = threading.Event(), threading.Event()
        barrier = threading.Barrier(6)
        class Response:
            headers = {'Content-Type': 'image/png'}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size): return b'PNG'
        def open_image(*args, **kwargs):
            entered.set()
            if not release.wait(3): raise RuntimeError('test timed out')
            return Response()
        app = SimpleNamespace()
        def read():
            barrier.wait(timeout=3)
            return server.nexon_image(app, 'https://open.api.nexon.com/static/maplestory/a.png')
        with patch('urllib.request.urlopen', side_effect=open_image) as opened, ThreadPoolExecutor(max_workers=6) as pool:
            jobs = [pool.submit(read) for _ in range(6)]
            self.assertTrue(entered.wait(3))
            time.sleep(0.03)
            release.set()
            self.assertEqual([j.result(timeout=3) for j in jobs], [(b'PNG', 'image/png')] * 6)
        self.assertEqual(opened.call_count, 1)

    def test_body_size_eviction_and_failure_retry(self):
        cache = server.ImageCache()
        with patch.object(server, 'IMAGE_CACHE_BYTES', 10):
            cache.get('a', lambda: (b'123456', 'image/png'))
            cache.get('b', lambda: (b'123456', 'image/png'))
        self.assertEqual(list(cache.entries), ['b'])
        self.assertEqual(cache.size, 6)
        with self.assertRaises(AppError):
            cache.get('bad', lambda: (_ for _ in ()).throw(AppError('실패')))
        self.assertFalse(cache.inflight)
        self.assertEqual(cache.get('bad', lambda: (b'PNG', 'image/png')), (b'PNG', 'image/png'))


if __name__ == '__main__':
    unittest.main()
