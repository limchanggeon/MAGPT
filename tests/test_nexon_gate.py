"""넥슨 호출 간격(초당 한도)과 한도 초과 뒤 다시 부르기. 실제 넥슨은 부르지 않는다."""
import unittest
from unittest import mock

from mepiti import adapters
from mepiti.core import AppError


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


class GateTests(unittest.TestCase):
    def test_calls_are_spaced(self):
        c = FakeClock()
        gate = adapters.Gate(0.25, c.clock, c.sleep)
        for _ in range(4):
            gate.wait()
        self.assertEqual(c.slept, [0.25, 0.25, 0.25])          # 첫 호출은 바로, 이후 0.25초 간격

    def test_rate_limit_is_retried(self):
        c = FakeClock()
        calls = []

        def fake_request(url, headers=None):
            calls.append(url)
            if len(calls) < 3:
                error = AppError('요청 한도를 초과했습니다.', 502)
                error.upstream = 429
                raise error
            return {'ocid': 'x'}

        class Key:
            def get(self): return 'k'
        with mock.patch.object(adapters, 'NEXON_GATE', adapters.Gate(0.25, c.clock, c.sleep)), \
                mock.patch.object(adapters, 'request_json', fake_request):
            self.assertEqual(adapters.Nexon(Key()).get('id', {'character_name': 'a'}), {'ocid': 'x'})
        self.assertEqual(len(calls), 3)
        self.assertGreaterEqual(sum(c.slept), 3.0)                    # 1초·2초 쉬고 다시

    def test_other_errors_are_not_retried(self):
        c = FakeClock()
        calls = []

        def fake_request(url, headers=None):
            calls.append(url)
            error = AppError('조회 대상을 찾지 못했습니다.', 502)
            error.upstream = 404
            raise error

        class Key:
            def get(self): return 'k'
        with mock.patch.object(adapters, 'NEXON_GATE', adapters.Gate(0.25, c.clock, c.sleep)), \
                mock.patch.object(adapters, 'request_json', fake_request):
            with self.assertRaises(AppError):
                adapters.Nexon(Key()).get('id', {'character_name': 'a'})
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
