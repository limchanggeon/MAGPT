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
    """랭킹·전투력·장비 응답 흉내. 호출 기록을 남긴다.

    이름에 전투력을 담는다: 'j25_0'은 같은 직업(렌) 2.5억, 'h26_0'은 히어로(같은 전사·STR 계열) 2.6억, 'b25_0'은 비숍(다른 계열).
    무릉 랭킹은 비어 있다(요즘 기록이 적다). dojang에 이름 목록을 주면 무릉 기록이 있는 것처럼 답한다.
    """
    SAME = [10, 12, 25, 26, 30, 24, 8, 25, 15, 27]          # 렌 레벨 랭킹 1쪽(억 × 10)

    def __init__(self, fail=None, dojang=()):
        self.calls, self.fail, self.dojang, self.ids = [], fail, list(dojang), {}

    def ocid(self, name):
        return self.ids.setdefault(name, f'o{len(self.ids) + 1:04d}')    # 실제 ocid처럼 이름과 다르게

    def name_of(self, ocid):
        return next(n for n, o in self.ids.items() if o == ocid)

    @staticmethod
    def cp_of(name):
        return 1.5e8 if name == '나' else int(name[1:].split('_')[0]) * 1e7

    def get(self, path, query):
        self.calls.append((path, dict(query)))
        if self.fail and len(self.calls) >= self.fail:
            error = AppError('요청 한도를 초과했습니다.', 502)
            error.upstream = 429
            raise error
        if path == 'id':
            return {'ocid': self.ocid(query['character_name'])}
        if path == 'character/stat':
            return {'final_stat': [{'stat_name': '전투력', 'stat_value': str(int(self.cp_of(self.name_of(query['ocid']))))}]}
        if path == 'ranking/overall' and 'ocid' in query:
            return {'ranking': [{'ranking': 3900, 'character_level': 291, 'class_name': '렌', 'sub_class_name': '',
                                 'world_name': '크로아', 'character_name': '나'}]}
        if path == 'ranking/dojang':
            return {'ranking': [{'character_name': n, 'dojang_floor': 80, 'character_level': 290} for n in self.dojang]}
        if path == 'ranking/overall' and 'class' in query:
            if query['page'] > 1:
                return {'ranking': []}
            return {'ranking': [{'character_name': f'j{c}_{i}', 'character_level': 290, 'class_name': '렌', 'sub_class_name': ''}
                                for i, c in enumerate(self.SAME)] + [{'character_name': '나', 'character_level': 291, 'class_name': '렌'}]}
        if path == 'ranking/overall':
            if query['page'] > 1:
                return {'ranking': []}
            return {'ranking': [{'character_name': f'h{c}_{i}', 'character_level': 290, 'class_name': '전사', 'sub_class_name': '히어로'}
                                for i, c in enumerate((25, 26, 9))]
                               + [{'character_name': f'b25_{i}', 'character_level': 290, 'class_name': '마법사', 'sub_class_name': '비숍'}
                                  for i in range(3)]
                               + [{'character_name': 'j99_0', 'character_level': 300, 'class_name': '렌', 'sub_class_name': ''}]}
        if path == 'character/item-equipment':
            name = self.name_of(query['ocid'])
            n = int(query['ocid'][-1])
            weapon = '히어로의 투핸드엑스' if name.startswith('h') else '렌의 장검'
            return {'item_equipment': [equip('모자', '에테르넬 나이트헬름', 22, '레전드리', '유니크'),
                                       equip('신발', '아케인셰이드 나이트슈즈', 21 + n % 2), equip('무기', weapon, 22)]}
        if path == 'character/set-effect':
            return {'set_effect': [{'set_name': '아케인셰이드 세트(전사)', 'total_set_count': 1,
                                    'set_option_full': [{'set_count': 2, 'set_option': '공격력  +30'}]}]}
        raise AssertionError(path)


class PeerTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(tempfile.mkdtemp())
        self.nexon = FakeNexon()
        self.peers = peers.Peers(self.store, lambda: self.nexon, sleep=lambda s: None)

    def collect(self):
        self.peers.choose('나', 2.5e8)
        while self.peers.step():
            pass

    def test_parse_target_power(self):
        self.assertEqual(peers.parse_cp('2억5천'), 2.5e8)
        self.assertEqual(peers.parse_cp('2억 5000'), 2.5e8)
        self.assertEqual(peers.parse_cp('3.2억'), 3.2e8)
        self.assertIsNone(peers.parse_cp('많이'))

    def test_choose_mixes_same_job_and_family_for_small_jobs(self):
        state = self.peers.choose('나', 2.5e8)
        target = state['target']
        self.assertEqual((target['job'], target['family'], target['pool'], target['family_pool']), ('렌-렌', '전사·STR', 10, 3))
        queue = self.store.setting(peers.QUEUE)
        self.assertEqual([q['name'][0] for q in queue[:4]], ['j', 'h', 'j', 'h'])      # 인원이 적어 번갈아
        self.assertFalse(any(q['name'].startswith('b') for q in queue))                 # 다른 계열(비숍)은 빼고
        self.assertNotIn('j99_0', [q['name'] for q in queue])                           # 전 직업 랭킹의 같은 직업은 중복이라 빼고
        self.assertNotIn('character/stat', [c[0] for c in self.nexon.calls])            # 고를 때는 전투력을 안 본다

    def test_dojang_records_come_first(self):
        self.nexon.dojang = ['d25_0']
        self.peers.choose('나', 2.5e8)
        self.assertEqual(self.store.setting(peers.QUEUE)[0]['name'], 'd25_0')

    def test_family_of(self):
        self.assertEqual(peers.family_of('렌')[0], '전사·STR')
        self.assertEqual(peers.family_of('캡틴')[0], '해적·DEX')
        self.assertIsNone(peers.family_of('제논')[0])
        self.assertEqual(peers.job_name({'class_name': '기사단', 'sub_class_name': '소울마스터'}), '소울마스터')

    def test_target_floor_interpolates(self):
        self.assertEqual(peers.target_floor([(60, 1e8), (80, 3e8)], 2e8), 70)
        self.assertEqual(peers.target_floor([(60, 1e8), (80, 3e8)], 9e8), 80)

    def test_screened_power_is_cached(self):
        self.peers.power('j20_9')
        before = len(self.nexon.calls)
        self.assertEqual(self.peers.power('j20_9')[1], 2e8)
        self.assertEqual(len(self.nexon.calls), before)

    def test_collect_saves_only_target_band_without_names(self):
        self.collect()
        rows = self.store.rows('SELECT * FROM peers')
        self.assertEqual(len(rows), 7)                                   # 렌 5명 + 히어로 2명(2.5억 ±15%)
        self.assertTrue(all(peers.in_band(r['cp'], 2.5e8) for r in rows))
        self.assertEqual(sum(r['family'] for r in rows), 2)
        dumped = json.dumps([dict(r) for r in rows], ensure_ascii=False)
        self.assertFalse(any(n in dumped for n in ('j25', 'h25', 'h26')))
        self.assertIn('아케인셰이드 세트(전사)', self.store.setting('set_tables'))    # 모르는 세트 단계표를 배움

    def test_daily_budget_stops_collection(self):
        self.peers.choose('나', 2.5e8)
        state = peers.calls(self.store)
        state['count'] = peers.daily_calls(self.store) - 2
        self.store.set_setting(peers.CALLS, state)
        before = len(self.store.setting(peers.QUEUE))
        self.assertFalse(self.peers.step())
        self.assertEqual(len(self.store.setting(peers.QUEUE)), before)

    def test_rate_limit_blocks_rest_of_day(self):
        self.peers.choose('나', 2.5e8)
        self.nexon.fail = len(self.nexon.calls) + 2
        self.peers.run()
        self.assertEqual(peers.left(self.store), 0)
        self.assertIn('한도', self.peers.error)

    def test_compare_flags_slots_behind(self):
        self.collect()
        profile = {'job': '렌', 'equipment': [
            {'slot': '모자', 'name': '하이네스 워리어헬름', 'starforce': 17, 'potential_grade': '에픽', 'additional_grade': '에픽'},
            {'slot': '신발', 'name': '아케인셰이드 나이트슈즈', 'starforce': 22, 'potential_grade': '유니크', 'additional_grade': '에픽'}]}
        result = peers.compare(self.store, profile)
        self.assertTrue(result['ready'])
        behind = {b['slot']: ' '.join(b['reasons']) for b in result['behind']}
        self.assertIn('중앙값 22성', behind['모자'])
        self.assertIn('에테르넬 나이트헬름 100%', behind['모자'])
        self.assertNotIn('신발', behind)
        text = peers.facts_text(result)
        self.assertIn('전투력 2.50억 ±15%', text)
        self.assertIn('같은 방어구·주스탯 계열(전사·STR) 2명', text)
        stats = peers.slot_stats(peers.stored(self.store, self.store.setting(peers.TARGET)))
        self.assertEqual(stats['무기']['count'], 5)                         # 무기는 같은 직업만
        self.assertEqual(stats['모자']['count'], 7)

    def test_other_job_is_not_compared(self):
        self.collect()
        self.assertFalse(peers.compare(self.store, {'job': '히어로', 'equipment': []})['ready'])

    def test_peer_questions_route_to_character(self):
        for q in ('비슷한 유저랑 비교해줘', '남들은 뭐 끼고 있어?', '뭐부터 바꿔야 해?', '목표 전투력대랑 비교해줘'):
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
        store.set_setting(peers.TARGET, {'job': '렌-렌', 'cp': 2.5e8, 'level': 291, 'at': '2026-10-01'})
        peers.ensure(store)
        worse = {'item_name': '아케인셰이드 나이트슈즈', 'item_equipment_slot': '신발', 'starforce': '17',
                 'item_total_option': {'str': '50'}}
        with store.db() as db:
            for i in range(5):
                data = {'신발': {'name': '아케인셰이드 나이트슈즈', 'starforce': 17, 'potential': '레전드리', 'item': worse}}
                db.execute('INSERT INTO peers(ocid,job,level,world_type,fetched_at,data,cp) VALUES(?,?,?,?,?,?,?)',
                           (f'o{i}', '렌-렌', 291, 0, peers.since()[:10] + 'T23:59:59+09:00', json.dumps(data, ensure_ascii=False), 2.5e8))
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


class LearnOrderTests(unittest.TestCase):
    def test_bands_with_hits_move_forward(self):
        target = {'screened': 0}
        queue = [{'name': 'now', 'level': 290}] + [{'name': f'a{i}', 'level': 270} for i in range(5)] + [{'name': f'b{i}', 'level': 295} for i in range(5)]
        for i in range(peers.LEARN_EVERY):
            target['screened'] += 1
            person = {'name': f'x{i}', 'level': 295 if i % 2 else 270}
            queue = peers.learn(target, person, hit=(person['level'] == 295), queue=queue)
        self.assertEqual(queue[0]['name'], 'now')                       # 지금 보는 후보는 그대로
        self.assertTrue(all(q['level'] == 295 for q in queue[1:6]))     # 해당이 나온 레벨대가 앞으로
