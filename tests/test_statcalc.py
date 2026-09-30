"""현재 스탯 재현(mepiti/statcalc.py). 실제 넥슨 응답 대신 작은 가짜 응답으로 식과 출처 분류를 확인한다."""
import copy
import unittest

from mepiti import statcalc as sc


def item(slot, name, str_=0, dex=0, att=0, all_stat=0, pots=()):
    out = {'item_equipment_slot': slot, 'item_name': name,
           'item_total_option': {'str': str(str_), 'dex': str(dex), 'int': '0', 'luk': '0', 'attack_power': str(att),
                                 'magic_power': '0', 'all_stat': str(all_stat), 'damage': '0'}}
    for i, line in enumerate(pots, 1):
        out[f'potential_option_{i}'] = line
    return out


def raw(final, items, **extra):
    stat = [{'stat_name': k, 'stat_value': str(v)} for k, v in final.items()]
    data = {'character/basic': {'character_level': 280}, 'character/stat': {'final_stat': stat},
            'character/item-equipment': {'item_equipment': items}, 'character/set-effect': {}, 'character/symbol-equipment': {},
            'character/hyper-stat': {}, 'user/union-raider': {}, 'user/union-artifact': {}, 'user/union-champion': {},
            'character/pet-equipment': {}, 'skills': []}
    data.update(extra)
    return data


class StatCalcTests(unittest.TestCase):
    def setUp(self):
        self.items = [item('모자', '모자', 300, 100, 50, 6, ('STR +10%', '올스탯 +7%', '캐릭터 기준 9레벨 당 STR +2')),
                      item('무기', '무기', 150, 150, 700)]
        # STR: 고정 AP 1000 + 300 + 150 + 레벨당 (280//9)*2=62 + 아니마 160 = 1672, % = 10+7+6 = 23, 미적용 심볼 13000
        self.final = {'STR': int(1672 * 1.23) + 13000, 'DEX': int((4 + 250) * 1.13), '공격력': int(750),
                      'AP 배분 STR': 1000, 'AP 배분 DEX': 4, '데미지': 50, '최종 데미지': 20,
                      '최대 스탯공격력': 0, '최소 스탯공격력': 0}
        self.skills = [{'skill_name': '메이플 용사', 'skill_effect': 'MP 70 소비 / [패시브 효과 : AP를 직접 투자한 모든 능력치 16% 증가]'},
                       {'skill_name': '샤프 아이즈', 'skill_effect': '270초 동안 크리티컬 확률 10% 증가'}]
        self.symbols = {'symbol': [{'symbol_name': '아케인심볼', 'symbol_str': '13000'}]}

    def ledger(self):
        return sc.build(raw(self.final, self.items, skills=self.skills, **{'character/symbol-equipment': self.symbols}))

    def test_reproduces_main_stat_with_classes(self):
        report = sc.reproduce(self.ledger())
        strow = report['rows'][0]
        self.assertEqual(report['main'], 'STR')
        self.assertEqual((strow['applied'], strow['pct'], strow['unapplied']), (1672, 23, 13000))
        self.assertEqual(strow['gap'], 0)

    def test_buff_skill_is_ignored(self):
        ledger = self.ledger()
        self.assertEqual(ledger.anima, 16)
        self.assertFalse(any('샤프' in r[1] for r in ledger.rows.get('STR', [])))

    def test_stat_attack_formula(self):
        # 사용자 대표 캐릭터 실제 값(2026-10-01): 무기 상수 1.30 × … = 최대 스탯공격력 67,298,953
        value = sc.stat_attack(48547, 4669, 6027, 97, 119.26, 1.3)
        self.assertAlmostEqual(value / 67298953, 1, places=4)

    def test_swap_range_and_direction(self):
        ledger = self.ledger()
        better = copy.deepcopy(self.items[0])
        better['potential_option_1'] = 'STR +13%'
        result = sc.swap(ledger, self.items[0], better)
        low, high = result['range']
        self.assertGreater(low, 0)
        self.assertLessEqual(low, high)
        self.assertEqual(result['delta']['STR'], (0, 3))

    def test_unexplained_gap_is_calibrated_both_ways(self):
        self.final['STR'] += 500               # 모르는 출처
        report, models = sc.calibrate(self.ledger())
        actual = self.final['STR']
        for way in ('flat', 'pct'):
            self.assertAlmostEqual(sc.value(models['STR'][way]), actual, delta=2)
        self.assertEqual(report['rows'][0]['gap'], 500)

    def test_set_option_text(self):
        self.assertEqual(sc.split_stats('올스탯  +50, 공격력  +40, 마력  +40, 보스 몬스터 데미지 +10%'),
                         [('ALL', 50, False), ('ATT', 40, False), ('MATT', 40, False)])


if __name__ == '__main__':
    unittest.main()


class SetAndBossTests(unittest.TestCase):
    FULL = {'도전자의 장비 세트(전사)': [{'set_count': 5, 'set_option': '공격력  +25, 보스 몬스터 데미지 +10%'},
                                   {'set_count': 6, 'set_option': '공격력  +30, 몬스터 방어율 무시 +10%'}],
            '에테르넬 세트(전사)': [{'set_count': 3, 'set_option': '올스탯  +50, 공격력  +40'},
                              {'set_count': 4, 'set_option': '공격력  +40, 보스 몬스터 데미지 +10%'}]}

    def ledger(self):
        items = [item('상의', '도전자의 상의', 100, 50, 10), item('하의', '도전자의 하의'), item('신발', '도전자의 신발'),
                 item('망토', '도전자의 망토'), item('어깨장식', '도전자의 어깨장식'),
                 item('모자', '에테르넬 나이트헬름'), item('장갑', '에테르넬 나이트글러브'), item('무기', '제네시스 창세검', 150, 150, 700)]
        sets = {'set_effect': [{'set_name': k, 'total_set_count': c, 'set_option_full': v, 'set_effect_info': []}
                               for (k, v), c in zip(self.FULL.items(), (6, 3))]}
        final = {'STR': 20000, 'DEX': 3000, '공격력': 3000, 'AP 배분 STR': 1000, 'AP 배분 DEX': 4, '데미지': 50, '최종 데미지': 20,
                 '보스 몬스터 데미지': 200, '방어율 무시': 90, '크리티컬 데미지': 70, '최대 스탯공격력': 0, '최소 스탯공격력': 0}
        return sc.build(raw(final, items, **{'character/set-effect': sets})), items

    def test_lucky_item_verifies_armor_sets(self):
        ledger, _ = self.ledger()
        self.assertTrue(all(s['verified'] for s in ledger.sets.values()))
        self.assertTrue(ledger.sets['도전자의 장비 세트(전사)']['lucky'])        # 5개 착용(3개 이상) → +1
        self.assertEqual(ledger.sets['에테르넬 세트(전사)']['pieces'], 3)       # 2개 + 제네시스 무기

    def test_lucky_needs_three_pieces_and_skips_accessory_sets(self):
        self.assertEqual(sc.set_count('아케인셰이드 세트(전사)', 2, True), 2)
        self.assertEqual(sc.set_count('아케인셰이드 세트(전사)', 3, True), 4)
        self.assertEqual(sc.set_count('보스 장신구 세트', 5, True), 5)
        self.assertEqual(sc.set_count('에테르넬 세트(전사)', 3, True), 3)

    def test_learned_table_for_unworn_set(self):
        ledger, items = self.ledger()
        tables = {'아케인셰이드 세트(전사)': [{'set_count': 2, 'set_option': '공격력  +30'}]}
        ledger = sc.build({**raw(ledger.final, items, **{'character/set-effect': {'set_effect': [
            {'set_name': k, 'total_set_count': c, 'set_option_full': v, 'set_effect_info': []}
            for (k, v), c in zip(self.FULL.items(), (6, 3))]}})}, tables)
        new = copy.deepcopy(items[1])
        new['item_name'] = '아케인셰이드 나이트팬츠'
        change = sc.set_change(ledger, items[1], new)
        self.assertIn('아케인셰이드 세트(전사) 0→1세트', change['notes'])     # 1개로는 세트 효과 없음
        self.assertEqual(change['unknown'], [])

    def test_set_change_when_moving_a_piece(self):
        ledger, items = self.ledger()
        new = copy.deepcopy(items[0])
        new['item_name'] = '에테르넬 나이트아머'
        change = sc.set_change(ledger, items[0], new)
        self.assertEqual(change['stats']['ATT'], (-30 + 40, 0))      # 도전자 6세트 잃고 에테르넬 4세트 얻음
        self.assertEqual(change['boss'], 10)
        self.assertEqual(change['ied_remove'], [10])
        result = sc.swap(ledger, items[0], new)
        self.assertLess(result['ied'][1], result['ied'][0])          # 방무 10%를 잃는다

    def test_combine_ied_is_multiplicative(self):
        self.assertAlmostEqual(sc.combine_ied(90, add=[10]), 91)
        self.assertAlmostEqual(sc.combine_ied(91, remove=[10]), 90)

    def test_crit_damage_line_raises_boss_score(self):
        ledger, items = self.ledger()
        new = copy.deepcopy(items[5])
        new['potential_option_1'] = '크리티컬 데미지 +8%'
        result = sc.swap(ledger, items[5], new)
        self.assertEqual(result['range'], [0.0, 0.0])
        self.assertGreater(result['boss_range'][0], 3)
