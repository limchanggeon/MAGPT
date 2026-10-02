"""수익 화면: 흐름 막대 고정(지난 주를 골라도 이번 주까지 보인다)과 캡처 입력의 재획비."""
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
