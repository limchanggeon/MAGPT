"""수익 화면: 흐름 막대 고정(지난 주를 골라도 이번 주까지 보인다)과 캡처 입력의 소재비."""
import tempfile
import unittest
from datetime import timedelta

from mepiti import earnings
from mepiti.core import Store


class TrendAnchorTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())

    def test_picking_past_week_keeps_recent_bars(self):
        this_week = earnings.week_start(earnings.today())
        past = this_week - timedelta(weeks=3)
        d = earnings.overview(self.store, week=past.isoformat())
        self.assertEqual(d['weeks'][-1]['week_start'], this_week.isoformat())      # 막대는 이번 주까지
        self.assertIn(past.isoformat(), [w['week_start'] for w in d['weeks']])
        self.assertEqual(d['week']['start'], past.isoformat())                      # 고른 주는 그대로

    def test_far_past_week_moves_window(self):
        far = earnings.week_start(earnings.today()) - timedelta(weeks=20)
        d = earnings.overview(self.store, week=far.isoformat())
        self.assertEqual(d['weeks'][-1]['week_start'], far.isoformat())

    def test_picking_past_month_keeps_recent_bars(self):
        this_month = earnings.today().replace(day=1)
        past = earnings.shift_month(this_month, -2)
        d = earnings.overview(self.store, month=past.isoformat()[:7])
        self.assertEqual(d['months'][-1]['month'], this_month.isoformat()[:7])
        self.assertEqual(d['month']['month'], past.isoformat()[:7])

    def test_selected_week_records_still_listed(self):
        past = earnings.week_start(earnings.today()) - timedelta(weeks=2)
        earnings.add(self.store, {'kind': 'hunt', 'day': past.isoformat(), 'meso': '5억', 'flasks': 2})
        d = earnings.overview(self.store, week=past.isoformat())
        self.assertEqual(len(d['hunts']), 1)
        self.assertEqual(d['week']['hunt'], 5e8)


if __name__ == '__main__':
    unittest.main()


class NexonImageProxyTests(unittest.TestCase):
    """넥슨 이미지를 앱이 대신 받아 주는 길(/nexon-image). 넥슨 정적 이미지 주소만 받는다."""
    def test_rejects_other_hosts_and_paths(self):
        from mepiti.core import AppError
        from mepiti.server import nexon_image
        class App: pass
        for url in ('http://open.api.nexon.com/static/maplestory/a.png', 'https://evil.example/static/maplestory/a.png',
                    'https://open.api.nexon.com/maplestory/v1/id', 'https://open.api.nexon.com:444/static/maplestory/a.png', ''):
            with self.assertRaises(AppError):
                nexon_image(App(), url)

    def test_caches_and_checks_content_type(self):
        from unittest import mock
        from mepiti import server
        class Response:
            headers = {'Content-Type': 'image/png'}
            def __init__(self): self.reads = 0
            def read(self, n): return b'PNG'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        class App: pass
        app = App()
        url = 'https://open.api.nexon.com/static/maplestory/character/look/ABC'
        with mock.patch('urllib.request.urlopen', return_value=Response()) as opened:
            self.assertEqual(server.nexon_image(app, url), (b'PNG', 'image/png'))
            self.assertEqual(server.nexon_image(app, url), (b'PNG', 'image/png'))
            self.assertEqual(opened.call_count, 1)


class CaptureZeroTests(unittest.TestCase):
    """사냥 전 0메소·0조각에서 시작한 캡처(2026-10-02 사용자 보고: 0 + @가 @가 안 되고 0)."""
    def read(self, raw):
        class Model:
            def read_capture(self, selected, mime, data):
                return raw, {}
        return earnings.read_capture(Model(), 'gemini', 'data:image/png;base64,AA')['values']

    def test_zero_meso_is_zero_not_missing(self):
        values = self.read({'inventory_meso': '0', 'storage_meso': '0 메소', 'sol_erda_pieces': '0', 'maple_points': None})
        self.assertEqual((values['inventory_meso'], values['storage_meso'], values['sol_erda_pieces']), (0.0, 0.0, 0))

    def test_missing_stays_missing(self):
        values = self.read({'inventory_meso': None, 'storage_meso': None, 'sol_erda_pieces': None, 'maple_points': None})
        self.assertIsNone(values['inventory_meso'])


class BossLimitTests(unittest.TestCase):
    """주간 보스는 캐릭터당 주 12개(검은 마법사 제외), 월보(검마)는 합계에서 따로."""
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())

    def test_thirteenth_weekly_boss_is_refused_per_character(self):
        from mepiti.core import AppError
        for i in range(12):
            earnings.add(self.store, {'kind': 'boss', 'boss': f'보스{i}', 'crystal': '1', 'character': '본캐'})
        with self.assertRaises(AppError):
            earnings.add(self.store, {'kind': 'boss', 'boss': '보스12', 'crystal': '1', 'character': '본캐'})
        earnings.add(self.store, {'kind': 'boss', 'boss': '검은 마법사 (하드)', 'crystal': '7', 'character': '본캐'})   # 검마는 제외
        earnings.add(self.store, {'kind': 'boss', 'boss': '보스0', 'crystal': '1', 'character': '부캐'})              # 다른 캐릭터는 따로
        d = earnings.overview(self.store)
        self.assertEqual(d['boss_counts'], {'본캐': 12, '부캐': 1})
        self.assertEqual(d['week']['monthly'], 7e8)                  # 월보(검마)는 주보와 따로
        self.assertEqual(d['week']['boss'], 13e8)
        self.assertEqual(d['week']['total'], 20e8)
        main = next(c for c in d['week']['characters'] if c['name'] == '본캐')
        self.assertEqual((main['boss'], main['monthly']), (12e8, 7e8))


class ScrollHistoryTests(unittest.TestCase):
    """강화권(주문서)으로 한 번에 오른 기록은 비용·기대값 비교에서 뺀다."""
    def row(self, i, before, after, result='성공', events=None):
        import json
        return {'id': str(i), 'character': '나', 'world': '크로아', 'item': '에테르넬 나이트헬름', 'before': before, 'after': after,
                'result': result, 'starcatch': None, 'safeguard': 0, 'created': f'2026-10-0{1 + i // 10}T{i % 10:02d}:00',
                'events': json.dumps(events or []), 'superior': 0}

    def test_scroll_jump_starts_comparison_after_it(self):
        from mepiti import history, conditions
        store = Store(tempfile.mkdtemp())
        rows = [self.row(0, 0, 18), self.row(1, 18, 18, '실패(유지)'), self.row(2, 18, 19)]
        g = history.analyse(store, '나', '에테르넬 나이트헬름', rows, conditions.DEFAULTS, {})
        self.assertEqual(g['start'], 18)
        self.assertEqual(g['attempts'], 2)                               # 강화권 기록은 시도로 세지 않는다
        self.assertTrue(any('강화권' in n for n in g['notes']))

    def test_one_plus_one_event_is_not_a_scroll(self):
        from mepiti import history
        self.assertFalse(history.scroll_jump(self.row(0, 5, 7, events=[{'plus': True}])))
        self.assertTrue(history.scroll_jump(self.row(0, 5, 7)))
        self.assertTrue(history.scroll_jump(self.row(0, 12, 17, events=[{'plus': True}])))


class PriceTableTests(unittest.TestCase):
    """시세표 이미지 → 노작값(확인 후 저장). 모델은 글자만 옮기고 단위 변환은 앱이 한다."""
    def model(self, raw):
        class M:
            def read_price_table(self, selected, mime, data):
                return raw, {}
        return M()

    def test_header_unit_and_inline_units(self):
        from mepiti import prices
        raw = {'unit': '억', 'server': '본 서버', 'rows': [{'item': '몽환의 벨트', 'price': '41.68'}, {'item': '에스텔라 이어링', 'price': '90만'},
                                                         {'item': '창세의 뱃지', 'price': '166.67'}, {'item': '알 수 없음', 'price': '-'}]}
        r = prices.read_table(self.model(raw), 'gemini', 'data:image/png;base64,AA')
        self.assertEqual([x['price'] for x in r['rows']], [4_168_000_000, 900_000, 16_667_000_000, None])

    def test_no_unit_means_eok_and_save_many(self):
        from mepiti import prices
        store = Store(tempfile.mkdtemp())
        raw = {'unit': None, 'server': None, 'rows': [{'item': '전사 모자', 'price': '0.30'}]}
        r = prices.read_table(self.model(raw), 'gemini', 'data:image/webp;base64,AA')
        self.assertEqual(r['rows'][0]['price'], 30_000_000)
        out = prices.save_many(store, [{'item': '아케인셰이드 전사 모자', 'price': '3000만'}, {'item': '', 'price': '1'}], '시세표 이미지')
        self.assertEqual(out['saved'], 1)
        self.assertEqual(store.price_lookup('아케인셰이드 전사 모자', None)['price'], 30_000_000)

    def test_rejects_non_image_and_local_model(self):
        from mepiti import prices
        from mepiti.adapters import ModelRouter
        from mepiti.core import AppError
        with self.assertRaises(AppError):
            prices.read_table(self.model({}), 'gemini', 'data:text/plain;base64,AA')
        with self.assertRaises(AppError):
            ModelRouter(object(), {'gemini': object()}).read_price_table('qwen3.5:2b', 'image/png', 'AA')


class SchedulerPriceTests(unittest.TestCase):
    """스케줄러는 난이도를 영어로 준다 — 결정석 표(한국어)와 맞춰 가격을 채운다(2026-10-03 실제 응답 확인)."""
    def test_english_difficulty_and_short_names(self):
        self.assertEqual(earnings.boss_label({'name': '세렌', 'difficulty': 'hard'}), '선택받은 세렌 (하드)')
        self.assertEqual(earnings.boss_label({'name': '스우', 'difficulty': 'extreme'}), '스우 (익스트림)')
        self.assertTrue(earnings.crystal_price('자쿰', 'chaos'))
        self.assertEqual(earnings.crystal_price('자쿰', 'chaos'), earnings.crystal_price('자쿰', '카오스'))

    def test_scheduler_rows_get_official_price(self):
        class Nexon:
            def scheduler(self, name):
                return {'character': name, 'bosses': [{'name': '세렌', 'difficulty': 'hard', 'complete': True, 'cycle': 'bossWeekly'},
                                                      {'name': '모르는보스', 'difficulty': 'hard', 'complete': True, 'cycle': 'bossWeekly'}]}
        store = Store(tempfile.mkdtemp())
        d = earnings.scheduled_bosses(store, Nexon(), ['본캐'])
        prices = {b['boss']: (b['price'], b['price_source']) for b in d['bosses']}
        self.assertEqual(prices['선택받은 세렌 (하드)'][1], 'official')
        self.assertIsNone(prices['모르는보스 (하드)'][0])          # 표에 없으면 직접 입력
