"""목표 전투력대 비교 상담(mepiti/consult.py)과 대화 연결. 실제 넥슨·AI는 부르지 않는다."""
import json
import tempfile
import unittest

from mepiti import chat, consult, peers, statcalc
from mepiti.core import Store


def item(slot, name, star=18, str_=100, pots=()):
    out = {'item_equipment_slot': slot, 'item_name': name, 'starforce': str(star),
           'item_total_option': {'str': str(str_), 'attack_power': '10'}}
    for i, p in enumerate(pots, 1):
        out[f'potential_option_{i}'] = p
    return out


CHILHEUK = [{'set_count': 2, 'set_option': '공격력  +10, 보스 몬스터 데미지 +10%'},
            {'set_count': 3, 'set_option': '공격력  +10, 몬스터 방어율 무시 +10%'}]


def setup_store():
    store = Store(tempfile.mkdtemp())
    store.set_setting(peers.TARGET, {'job': '렌-렌', 'cp': 2.5e8, 'level': 291, 'at': '2026-10-01'})
    store.set_setting(statcalc.SET_TABLES, {'칠흑의 보스 세트': CHILHEUK})
    peers.ensure(store)
    people = {'벨트': item('벨트', '몽환의 벨트', 18, 200, ('STR +12%',)),
              '반지1': item('반지1', '거대한 공포', 18, 200, ('STR +12%',)),
              '펜던트': item('펜던트', '고통의 근원', 18, 200, ('STR +12%',))}
    with store.db() as db:
        for i in range(6):
            data = {slot: {'name': it['item_name'], 'starforce': 18, 'potential': '레전드리', 'item': it} for slot, it in people.items()}
            db.execute('INSERT INTO peers(ocid,job,level,world_type,fetched_at,data,cp) VALUES(?,?,?,?,?,?,?)',
                       (f'o{i}', '렌-렌', 291, 0, peers.since()[:10] + 'T23:59:59+09:00', json.dumps(data, ensure_ascii=False), 2.5e8))
    mine = [item('벨트', '골든 클로버 벨트', 17, 50), item('반지1', '리스트레인트 링', 0, 0), item('반지2', '어웨이크 링', 0, 0),
            item('펜던트', '매커네이터 펜던트', 15, 60), item('무기', '제네시스 창세검', 22, 150)]
    final = [{'stat_name': k, 'stat_value': str(v)} for k, v in {
        'STR': 40000, 'DEX': 4000, '공격력': 5000, 'AP 배분 STR': 1400, '데미지': 90, '최종 데미지': 100,
        '보스 몬스터 데미지': 200, '방어율 무시': 90, '크리티컬 데미지': 70}.items()]
    ledger = statcalc.build({'character/basic': {'character_level': 291}, 'character/stat': {'final_stat': final},
                             'character/item-equipment': {'item_equipment': mine}, 'character/set-effect': {},
                             'character/symbol-equipment': {}, 'character/hyper-stat': {}, 'user/union-raider': {},
                             'user/union-artifact': {}, 'user/union-champion': {}, 'character/pet-equipment': {}, 'skills': []},
                            store.setting(statcalc.SET_TABLES))
    profile = {'job': '렌', 'equipment': [{'slot': i['item_equipment_slot'], 'name': i['item_name'], 'starforce': int(i['starforce'])}
                                          for i in mine]}
    return store, ledger, profile


class ConsultParsingTests(unittest.TestCase):
    def test_target_power_in_question(self):
        self.assertEqual(consult.target_cp_in('목표 전투력 2억5천이면 뭐부터?'), 2.5e8)
        self.assertEqual(consult.target_cp_in('3억까지 가려면'), 3e8)
        self.assertIsNone(consult.target_cp_in('반지는 뭘로 바꿔?'))

    def test_slots_items_sets(self):
        self.assertEqual(consult.slots_in('반지는 뭘로 바꿔?', {'반지1', '반지2', '벨트'}), ['반지1', '반지2'])
        self.assertEqual(consult.items_in('몽환으로 바꾸면?', ['몽환의 벨트', '골든 클로버 벨트']), ['몽환의 벨트'])
        self.assertEqual(consult.sets_in('칠흑 맞추면?', ['칠흑의 보스 세트', '보스 장신구 세트']), ['칠흑의 보스 세트'])


class ConsultBuildTests(unittest.TestCase):
    def test_slot_question_uses_best_ring_slot(self):
        store, ledger, profile = setup_store()
        text, _, _ = consult.build(store, profile, ledger, '반지는 뭘로 바꿔?')
        self.assertIn('[질문한 부위: 반지]', text)
        self.assertIn('거대한 공포', text)

    def test_set_question_combines_once_per_item(self):
        store, ledger, profile = setup_store()
        text, _, _ = consult.build(store, profile, ledger, '칠흑 세트 맞추면 얼마나 올라?')
        line = next(l for l in text.splitlines() if l.startswith('[함께 바꾸면: 칠흑의 보스 세트 맞추기]'))
        self.assertIn('3부위', line)                          # 벨트·반지·펜던트 — 같은 반지를 두 자리에 넣지 않는다
        self.assertIn('칠흑의 보스 세트 0→3세트', line)
        self.assertIn('%~', line)

    def test_money_question_without_prices_says_unknown(self):
        store, ledger, profile = setup_store()
        text, _, _ = consult.build(store, profile, ledger, '가성비는?')
        self.assertIn('[값 모름]', text)
        self.assertNotIn('[가성비]', text)

    def test_genesis_weapon_is_never_a_candidate(self):
        store, ledger, profile = setup_store()
        text, _, _ = consult.build(store, profile, ledger, '무기 뭘로 바꿔?')
        self.assertNotIn('제네시스 창세검 →', text)


class ConsultChatTests(unittest.TestCase):
    def test_number_guard_reads_particles(self):
        self.assertEqual(chat.unsupported_numbers('보스 기준 +28.45만큼 올라요', '보스 기준 +28.45%'), [])
        self.assertEqual(chat.unsupported_numbers('1억당 효과로 보면', '아무 사실'), [])
        self.assertTrue(chat.unsupported_numbers('3억 들어요', '아무 사실'))

    def test_consult_style_prompt(self):
        from mepiti.adapters import analysis_messages
        plain = analysis_messages('사실', '질문')[0]['content']
        styled = analysis_messages('사실', '질문', consult=True)[0]['content']
        self.assertIn('항목별로 짧게', plain)
        self.assertIn('상담', styled)
        self.assertNotIn('항목별로 짧게', styled)

    def test_followup_stays_in_consultation(self):
        self.assertTrue(chat.PEER_INTENT.search('반지는 뭘로 바꿔?'))
        self.assertTrue(chat.PEER_INTENT.search('칠흑 세트 맞추면 얼마나 올라?'))
        self.assertTrue(chat.CHARACTER_INTENT.search('펜던트 뭐로 바꾸지'))


if __name__ == '__main__':
    unittest.main()


class UniqueEquipTests(unittest.TestCase):
    """반지·펜던트는 같은 장비를 두 자리에 낄 수 없다 — 자리마다 다른 장비를 추천한다."""
    def test_ring_slots_get_distinct_items_and_worn_items_are_skipped(self):
        store, ledger, profile = setup_store()
        ring = item('반지2', '거대한 공포', 18, 200, ('STR +12%',))
        meister = item('반지1', '마이스터링', 18, 150, ('STR +9%',))
        people = [{'반지1': {'name': '거대한 공포', 'starforce': 18, 'item': {**ring, 'item_equipment_slot': '반지1'}},
                   '반지2': {'name': '거대한 공포', 'starforce': 18, 'item': ring}},
                  {'반지1': {'name': '마이스터링', 'starforce': 18, 'item': meister},
                   '반지2': {'name': '어웨이크 링', 'starforce': 0, 'item': item('반지2', '어웨이크 링', 0, 0)}}]
        sims = peers.simulate(people, peers.slot_stats(people), ledger)
        picked = [sims[s]['item'] for s in ('반지1', '반지2') if s in sims]
        self.assertEqual(len(picked), len(set(picked)))                    # 같은 장비를 두 자리에 추천하지 않는다
        self.assertNotIn('어웨이크 링', [sims[s]['item'] for s in ('반지1',) if s in sims])   # 반지2에 이미 낀 장비는 반지1 후보가 아니다
