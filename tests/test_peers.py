"""비슷한 유저 장비 통계(mepiti/peers.py)와 재획 캡처 읽기(earnings.read_capture). 실제 넥슨·AI는 부르지 않는다."""
import json
import tempfile
import unittest

from mepiti import adapters, chat, earnings, peers
from mepiti.adapters import ModelRouter
from mepiti.core import AppError, Store
from mepiti.prices import parse_price


def equip(slot, name, star, pot='유니크', add='에픽'):
    return {'item_equipment_slot': slot, 'item_name': name, 'starforce': str(star),
            'potential_option_grade': pot, 'additional_potential_option_grade': add}


class FakeNexon:
    """랭킹·장비 응답 흉내. 호출 기록을 남긴다."""
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def get(self, path, query):
        self.calls.append((path, dict(query)))
        if self.fail and len(self.calls) >= self.fail:
            error = AppError('요청 한도를 초과했습니다.', 502)
            error.upstream = 429
            raise error
        if path == 'id':
            return {'ocid': 'x' + query['character_name'][-1]}   # 실제 ocid처럼 이름이 들어 있지 않게
        if path == 'ranking/overall' and 'ocid' in query and 'class' not in query:
            return {'ranking': [{'ranking': 67834, 'character_level': 291, 'class_name': '렌', 'sub_class_name': '',
                                 'world_name': '크로아', 'character_name': '나'}]}
        if path == 'ranking/overall' and 'ocid' in query:
            return {'ranking': [{'ranking': 450, 'character_level': 291, 'class_name': '렌', 'sub_class_name': ''}]}
        if path == 'ranking/overall':
            levels = [295, 291, 290, 280, 292, 291, 288]
            return {'ranking': [{'character_name': f'유저{i}', 'character_level': lv, 'class_name': '렌'}
                                for i, lv in enumerate(levels)] + [{'character_name': '나', 'character_level': 291}]}
        if path == 'character/item-equipment':
            n = int(query['ocid'][-1])
            return {'item_equipment': [equip('모자', '에테르넬 나이트헬름', 22, '레전드리', '유니크'),
                                       equip('신발', '아케인셰이드 나이트슈즈', 21 + n % 2)]}
        raise AssertionError(path)


class PeerTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())
        self.nexon = FakeNexon()
        self.peers = peers.Peers(self.store, lambda: self.nexon, sleep=lambda s: None)

    def test_choose_uses_job_rank_page_and_level_band(self):
        state = self.peers.choose('나')
        self.assertEqual(state['target']['job'], '렌-렌')              # 전직이 없는 직업은 같은 이름 두 번
        page = self.nexon.calls[-1][1]
        self.assertEqual((page['class'], page['page']), ('렌-렌', 3))  # 직업 안 450위 → 3쪽
        queue = self.store.setting(peers.QUEUE)
        self.assertEqual([q['level'] for q in queue], [291, 291, 290, 292, 288])   # ±3, 가까운 레벨 먼저, 나 제외
        self.assertNotIn('나', [q['name'] for q in queue])

    def test_collect_saves_summary_without_names(self):
        self.peers.choose('나')
        while self.peers.step():
            pass
        rows = self.store.rows('SELECT * FROM peers')
        self.assertEqual(len(rows), 5)
        self.assertNotIn('유저', json.dumps([dict(r) for r in rows], ensure_ascii=False))
        self.assertEqual(json.loads(rows[0]['data'])['모자']['starforce'], 22)

    def test_daily_budget_stops_collection(self):
        self.peers.choose('나')
        state = peers.calls(self.store)
        state['count'] = peers.DAILY_CALLS - 1
        self.store.set_setting(peers.CALLS, state)
        self.assertFalse(self.peers.step())                           # 한 명에 2회가 필요하다
        self.assertEqual(len(self.store.setting(peers.QUEUE)), 5)

    def test_rate_limit_blocks_rest_of_day(self):
        self.nexon.fail = 6
        self.peers.choose('나')
        self.peers.run()
        self.assertEqual(peers.left(self.store), 0)
        self.assertIn('한도', self.peers.error)

    def test_compare_flags_slots_behind(self):
        self.peers.choose('나')
        while self.peers.step():
            pass
        profile = {'job': '렌', 'equipment': [
            {'slot': '모자', 'name': '하이네스 워리어헬름', 'starforce': 17, 'potential_grade': '에픽', 'additional_grade': '에픽'},
            {'slot': '신발', 'name': '아케인셰이드 나이트슈즈', 'starforce': 22, 'potential_grade': '유니크', 'additional_grade': '에픽'}]}
        result = peers.compare(self.store, profile)
        self.assertTrue(result['ready'])
        behind = {b['slot']: ' '.join(b['reasons']) for b in result['behind']}
        self.assertIn('중앙값 22성', behind['모자'])
        self.assertIn('레전드리', behind['모자'])
        self.assertIn('에테르넬 나이트헬름 100%', behind['모자'])
        self.assertNotIn('신발', behind)
        text = peers.facts_text(result)
        self.assertIn('유저 5명', text)
        self.assertIn('[비슷한 유저보다 뒤처진 부위]', text)

    def test_other_job_is_not_compared(self):
        self.peers.choose('나')
        while self.peers.step():
            pass
        self.assertFalse(peers.compare(self.store, {'job': '히어로', 'equipment': []})['ready'])

    def test_peer_questions_route_to_character(self):
        for q in ('비슷한 유저랑 비교해줘', '남들은 뭐 끼고 있어?', '뭐부터 바꿔야 해?'):
            self.assertTrue(chat.PEER_INTENT.search(q), q)
        self.assertTrue(chat.CHARACTER_INTENT.search('비슷한 유저랑 비교해줘'))


class CaptureTests(unittest.TestCase):
    def test_game_meso_display(self):
        self.assertEqual(parse_price('2764만 4807'), 27644807)
        self.assertEqual(parse_price('11억'), 1_100_000_000)
        self.assertEqual(parse_price('1억 5천만'), 150_000_000)

    def test_read_capture_parses_values(self):
        class Model:
            def read_capture(self, selected, mime, data):
                self.args = (selected, mime, data)
                return {'inventory_meso': '2764만 4807', 'storage_meso': '11억', 'sol_erda_pieces': '12+147',
                        'maple_points': None}, {'model': 'm'}
        model = Model()
        out = earnings.read_capture(model, 'gemini', 'data:image/png;base64,AAAA')
        self.assertEqual(model.args, ('gemini', 'image/png', 'AAAA'))
        self.assertEqual(out['values'], {'inventory_meso': 27644807, 'storage_meso': 1_100_000_000,
                                         'sol_erda_pieces': 159, 'maple_points': None})

    def test_rejects_non_image(self):
        with self.assertRaises(AppError):
            earnings.read_capture(None, 'gemini', 'data:text/plain;base64,AAAA')

    def test_local_model_cannot_read(self):
        router = ModelRouter(object(), {'gemini': object()})
        with self.assertRaises(AppError) as caught:
            router.read_capture('qwen3.5:2b', 'image/png', 'AAAA')
        self.assertIn('직접', str(caught.exception))

    def test_reference_icon_is_sent_first(self):
        images = adapters.capture_images('image/png', 'CAP')
        self.assertEqual(len(images), 2)
        self.assertEqual(images[1], ('image/png', 'CAP'))
        self.assertTrue(adapters.PIECE_ICON.exists())

    def test_gemini_request_carries_both_images(self):
        sent = {}
        gemini = adapters.Gemini(None, lambda: None)
        gemini.model = 'gemini-flash-lite-latest'
        def call(path, body=None, key=None, timeout=60):
            sent['body'] = body
            return {'candidates': [{'content': {'parts': [{'text': json.dumps(
                {'inventory_meso': '1억', 'storage_meso': None, 'sol_erda_pieces': None, 'maple_points': None})}]}}]}
        gemini.call = call
        raw, _ = gemini.read_capture('image/png', 'CAP')
        parts = sent['body']['contents'][-1]['parts']
        self.assertEqual([('inlineData' in p) for p in parts], [True, True, False])
        self.assertEqual(raw['inventory_meso'], '1억')

    def test_hunt_days(self):
        days = earnings.hunt_days([{'day': '2026-09-03', 'total': 10, 'meso': 8, 'pieces': 1, 'flasks': 2},
                                   {'day': '2026-09-03', 'total': 5, 'meso': 5, 'pieces': 0, 'flasks': None}])
        self.assertEqual(days['2026-09-03'], {'total': 15, 'meso': 13, 'pieces': 1, 'flasks': 2, 'count': 2})


if __name__ == '__main__':
    unittest.main()


class SimulationRankingTests(unittest.TestCase):
    def test_worse_swaps_are_not_recommended(self):
        store = Store(tempfile.mkdtemp())
        store.set_setting(peers.TARGET, {'job': '렌-렌', 'level': 291, 'world_type': 0, 'at': '2026-10-01'})
        peers.ensure(store)
        worse = {'item_name': '아케인셰이드 나이트슈즈', 'item_equipment_slot': '신발', 'starforce': '17',
                 'item_total_option': {'str': '50'}}
        with store.db() as db:
            for i in range(5):
                data = {'신발': {'name': '아케인셰이드 나이트슈즈', 'starforce': 17, 'potential': '레전드리', 'item': worse}}
                db.execute('INSERT INTO peers(ocid,job,level,world_type,fetched_at,data) VALUES(?,?,?,?,?,?)',
                           (f'o{i}', '렌-렌', 291, 0, peers.since()[:10] + 'T23:59:59+09:00', json.dumps(data, ensure_ascii=False)))
        from mepiti import statcalc
        mine = {'item_name': '도전자의 신발', 'item_equipment_slot': '신발', 'starforce': '22',
                'item_total_option': {'str': '300', 'attack_power': '100'}, 'potential_option_1': 'STR +9%'}
        final = [{'stat_name': k, 'stat_value': str(v)} for k, v in {'STR': 40000, 'DEX': 4000, '공격력': 5000, 'AP 배분 STR': 1400,
                 '데미지': 90, '최종 데미지': 100, '보스 몬스터 데미지': 200, '방어율 무시': 90, '크리티컬 데미지': 70}.items()]
        ledger = statcalc.build({'character/basic': {'character_level': 291}, 'character/stat': {'final_stat': final},
                                 'character/item-equipment': {'item_equipment': [mine]}, 'character/set-effect': {},
                                 'character/symbol-equipment': {}, 'character/hyper-stat': {}, 'user/union-raider': {},
                                 'user/union-artifact': {}, 'user/union-champion': {}, 'character/pet-equipment': {}, 'skills': []})
        profile = {'job': '렌', 'equipment': [{'slot': '신발', 'name': '도전자의 신발', 'starforce': 22, 'potential_grade': '유니크'}]}
        result = peers.compare(store, profile, ledger=ledger)
        self.assertTrue(result['simulated'])
        self.assertEqual([a['slot'] for a in result['ahead']], ['신발'])
        self.assertFalse(any(r.startswith('장비 ') for b in result['behind'] for r in b['reasons']))
        self.assertIn('[바꾸면 오히려 손해인 부위]', peers.facts_text(result))
