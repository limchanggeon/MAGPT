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
