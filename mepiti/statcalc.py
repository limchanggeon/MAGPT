"""현재 스탯 재현 — 넥슨 Open API로 받은 스탯 출처를 모아 스탯창 값(주스탯·부스탯·공격력·스탯공격력)을 다시 계산한다.

장비 교체 시뮬레이션의 전 단계다. 스탯창 값을 출처에서 어느 정도 오차로 되살리는지 먼저 확인하고,
되살리지 못한 부분(패시브 등 API에 수치로 없는 것)은 '보정값'으로 남긴다.

식(커뮤니티에서 쓰는 스탯창 구조):
  스탯 = ⌊(% 적용 고정치 합) × (1 + % 합)⌋ + % 미적용 고정치 합
  최대 스탯공격력 = 무기 상수 × (4 × 주스탯 + 부스탯) × 공격력 × 0.01 × (1 + 데미지%) × (1 + 최종 데미지%)
어느 출처가 % 적용을 받는지는 CLASSIFY로 바꿔 가며 검증한다(확정되지 않은 부분).
"""
import math
import re

STATS = ('STR', 'DEX', 'INT', 'LUK')
SUB = {'STR': 'DEX', 'DEX': 'STR', 'INT': 'LUK', 'LUK': 'DEX'}
KEY = {'올스탯': 'ALL', 'ALLSTAT': 'ALL', '공격력': 'ATT', '마력': 'MATT', '힘': 'STR', '민첩': 'DEX', '지능': 'INT', '행운': 'LUK'}
SKILL_GRADES = ('0', '1', '1.5', '2', '2.5', '3', '4', 'hyperpassive', '5', '6')
PATHS = ('character/basic', 'character/stat', 'character/item-equipment', 'character/set-effect', 'character/symbol-equipment',
         'character/hyper-stat', 'user/union-raider', 'user/union-artifact', 'user/union-champion', 'character/pet-equipment')

# 출처 종류별로 % 적용을 받는지. True = % 적용, False = % 미적용. (검증 대상)
CLASSIFY = {'AP': True, '장비': True, '잠재': True, '세트': True, '스킬': True, '아니마·메이플 용사': True,
            '유니온 공격대원': False, '유니온 아티팩트': True, '챔피언': True, '펫': True, '하이퍼스탯': False, '심볼': False}
# 2026-10-01 세 캐릭터(렌·히어로·아크메이지) 검증: 유니온 공격대원 효과를 % 미적용으로 둘 때 부스탯 오차가 가장 작다(1.5~6.8%).
# 주스탯 3.5~4.7%, 공격력·마력 7~12%는 여전히 설명되지 않는다(헥사 스탯·길드 등 API에 수치로 없는 출처로 추정).


def num(value):
    try:
        return int(float(str(value).replace(',', '')))
    except (TypeError, ValueError):
        return 0


def key_of(word):
    return KEY.get(word, word)


class Ledger:
    """스탯별 출처 목록. rows[stat] = [(출처 종류, 설명, 값, 'flat'|'pct')]."""

    def __init__(self):
        self.rows = {}

    def add(self, stat, source, label, value, kind='flat'):
        if value:
            if stat == 'ALL':
                for s in STATS:
                    self.rows.setdefault(s, []).append((source, label, value, kind))
            else:
                self.rows.setdefault(stat, []).append((source, label, value, kind))

    def total(self, stat, kind, classify=None, applied=None):
        classify = classify or CLASSIFY
        out = 0
        for source, _, value, k in self.rows.get(stat, []):
            if k != kind:
                continue
            if applied is None or classify.get(source, True) == applied:
                out += value
        return out


LINE = re.compile(r'(STR|DEX|INT|LUK|올스탯|공격력|마력) \+(\d+)(%?)$')
PER_LEVEL = re.compile(r'캐릭터 기준 (\d+)레벨 당 (STR|DEX|INT|LUK|올스탯|공격력|마력) \+(\d+)$')


def potential_lines(item):
    for prefix in ('potential_option_', 'additional_potential_option_'):
        for i in (1, 2, 3):
            line = item.get(prefix + str(i))
            if line:
                yield line


def item_sources(ledger, item, level, where='장비'):
    """장비 한 개의 기여분. 교체 시뮬레이션에서 빼고 더할 단위다."""
    name = item.get('item_name') or ''
    total = item.get('item_total_option') or {}
    for s in STATS:
        ledger.add(s, '장비', name, num(total.get(s.lower())))
    ledger.add('ALL', '장비', name + ' 추옵 올스탯%', num(total.get('all_stat')), 'pct')
    ledger.add('ATT', '장비', name, num(total.get('attack_power')))
    ledger.add('MATT', '장비', name, num(total.get('magic_power')))
    ledger.add('DAMAGE', '장비', name, num(total.get('damage')), 'pct')
    for line in potential_lines(item):
        m = LINE.match(line)
        if m:
            ledger.add(key_of(m[1]), '잠재', f'{name} {line}', int(m[2]), 'pct' if m[3] else 'flat')
            continue
        m = PER_LEVEL.match(line)
        if m:
            ledger.add(key_of(m[2]), '잠재', f'{name} {line}', level // int(m[1]) * int(m[3]))
            continue
        m = re.match(r'데미지 \+(\d+)%$', line)
        if m:
            ledger.add('DAMAGE', '잠재', f'{name} {line}', int(m[1]), 'pct')
    soul = item.get('soul_option') or ''
    m = re.match(r'(공격력|마력) \+(\d+)(%?)$', soul)
    if m:
        ledger.add(key_of(m[1]), '잠재', f'{name} 소울 {soul}', int(m[2]), 'pct' if m[3] else 'flat')


def split_stats(text):
    """'올스탯  +50, 공격력  +40' 같은 효과 문장에서 (키, 값, %여부)를 뽑는다."""
    out = []
    for part in re.split(r',|/(?=\s*\S+\s)', text):
        part = part.strip()
        m = re.match(r'(올스탯|ALLSTAT|STR|DEX|INT|LUK|공격력|마력)\s*:?\s*\+?\s*(\d+)(%?)(?:\s*증가)?$', part)
        if m:
            out.append((key_of(m[1]), int(m[2]), bool(m[3])))
    return out


def skill_sources(ledger, skills):
    """패시브 스킬(버프 제외)의 고정 스탯·공격력. 'N초 동안'이 붙은 효과는 버프라 뺀다(패시브 효과 부분만 본다)."""
    for s in skills:
        effect = s.get('skill_effect') or ''
        if '[패시브 효과' in effect:
            effect = effect.split('[패시브 효과', 1)[1]
        elif re.search(r'\d+초 동안', effect):
            continue
        name = s.get('skill_name') or ''
        m = re.search(r'AP를 직접 투자한 모든 능력치 (\d+)% 증가', effect)
        if m:
            ledger.anima = int(m[1])
            continue
        m = re.search(r'영구적으로 힘 (\d+), 민첩 (\d+), 지능 (\d+), 행운 (\d+)', effect)
        if m:
            for stat, v in zip(STATS, m.groups()):
                ledger.add(stat, '스킬', name, int(v))
        for m in re.finditer(r'올스탯 (\d+) 증가', effect):
            ledger.add('ALL', '스킬', name, int(m[1]))
        for m in re.finditer(r'(?<![가-힣])(힘|민첩|지능|행운|STR|DEX|INT|LUK) (\d+) 증가', effect):
            ledger.add(key_of(m[1]), '스킬', name, int(m[2]))
        for m in re.finditer(r'공격력(?:, 마력|/마력|과 마력)? (\d+)(%?)', effect):
            ledger.add('ATT', '스킬', name, int(m[1]), 'pct' if m[2] else 'flat')
        for m in re.finditer(r'마력 (\d+)(%?) 증가', effect):
            if '공격력' not in effect:
                ledger.add('MATT', '스킬', name, int(m[1]), 'pct' if m[2] else 'flat')


def build(raw):
    """raw = {API 경로: 응답, 'skills': [스킬...]} → Ledger."""
    ledger = Ledger()
    ledger.anima = 0
    stat = {x['stat_name']: x['stat_value'] for x in (raw['character/stat'].get('final_stat') or [])}
    ledger.final = stat
    level = num((raw.get('character/basic') or {}).get('character_level')) or 0
    ledger.level = level
    equipment = raw['character/item-equipment']
    for item in equipment.get('item_equipment') or []:
        item_sources(ledger, item, level)
    title = equipment.get('title') or {}
    if title.get('title_description') and title.get('date_option_expire') != 'expired':
        for line in title['title_description'].splitlines():
            for k, v, pct in split_stats(line.strip('- ').replace('공격력/마력', '공격력')):
                ledger.add(k, '장비', '칭호 ' + (title.get('title_name') or ''), v, 'pct' if pct else 'flat')
    for s in raw['character/set-effect'].get('set_effect') or []:
        for o in s.get('set_effect_info') or []:
            for k, v, pct in split_stats(o.get('set_option') or ''):
                ledger.add(k, '세트', f"{s['set_name']} {o['set_count']}세트", v, 'pct' if pct else 'flat')
    for y in raw['character/symbol-equipment'].get('symbol') or []:
        for s in STATS:
            ledger.add(s, '심볼', y.get('symbol_name') or '', num(y.get('symbol_' + s.lower())))
    hyper = raw['character/hyper-stat']
    for x in hyper.get('hyper_stat_preset_' + str(hyper.get('use_preset_no') or 1)) or []:
        text = x.get('stat_increase') or ''
        for m in re.finditer(r'(STR|DEX|INT|LUK) (\d+) 증가', text):
            ledger.add(m[1], '하이퍼스탯', text, int(m[2]))
        m = re.match(r'공격력과 마력 (\d+) 증가', text)
        if m:
            ledger.add('ATT', '하이퍼스탯', text, int(m[1]))
            ledger.add('MATT', '하이퍼스탯', text, int(m[1]))
        m = re.match(r'데미지 (\d+)% 증가', text)
        if m:
            ledger.add('DAMAGE', '하이퍼스탯', text, int(m[1]), 'pct')
    union = raw['user/union-raider']
    for line in (union.get('union_raider_stat') or []) + (union.get('union_state_stat') or []):
        m = re.match(r'STR, DEX, LUK (\d+) 증가', line)
        if m:
            for s in ('STR', 'DEX', 'LUK'):
                ledger.add(s, '유니온 공격대원', line, int(m[1]))
            continue
        m = re.match(r'(STR|DEX|INT|LUK|ALLSTAT) (\d+)', line)
        if m:
            ledger.add(key_of(m[1]), '유니온 공격대원', line, int(m[2]))
        m = re.match(r'공격력(?:/마력)? (\d+) 증가', line)
        if m:
            ledger.add('ATT', '유니온 공격대원', line, int(m[1]))
        m = re.match(r'(?:공격력/)?마력 (\d+) 증가', line)
        if m and '마력' in line:
            ledger.add('MATT', '유니온 공격대원', line, int(m[1]))
    for x in raw['user/union-artifact'].get('union_artifact_effect') or []:
        for k, v, pct in split_stats((x.get('name') or '').replace(' 증가', '').replace('공격력 ', '공격력 +').replace('올스탯 ', '올스탯 +').replace('마력 ', '마력 +')):
            ledger.add(k, '유니온 아티팩트', x['name'], v, 'pct' if pct else 'flat')
    for x in raw['user/union-champion'].get('champion_badge_total_info') or []:
        text = (x.get('stat') or '').replace('공격력/마력', '공격력').replace(' 증가', '')
        for k, v, pct in split_stats(text.replace('올스탯 ', '올스탯 +').replace('공격력 ', '공격력 +')):
            ledger.add(k, '챔피언', x['stat'], v, 'pct' if pct else 'flat')
    pets = raw['character/pet-equipment']
    for i in (1, 2, 3):
        eq = pets.get(f'pet_{i}_equipment') or {}
        for o in eq.get('item_option') or []:
            k = key_of(o.get('option_type'))
            if k in ('ATT', 'MATT', *STATS):
                ledger.add(k, '펫', eq.get('item_name') or '', num(o.get('option_value')))
    skill_sources(ledger, raw.get('skills') or [])
    for s in STATS:
        ap = num(stat.get('AP 배분 ' + s))
        ledger.add(s, 'AP', 'AP 배분', ap)
        if ledger.anima:
            ledger.add(s, '아니마·메이플 용사', f'AP 투자 {ledger.anima}%', math.floor(ap * ledger.anima / 100))
    return ledger


def main_stat(final):
    return max(STATS, key=lambda s: num(final.get(s)))


def predict(ledger, stat, classify=None):
    applied = ledger.total(stat, 'flat', classify, True)
    unapplied = ledger.total(stat, 'flat', classify, False)
    pct = ledger.total(stat, 'pct')
    if stat in STATS:
        pct += 0     # 올스탯%는 add('ALL')이 이미 네 스탯에 나눠 넣었다
    return math.floor(applied * (1 + pct / 100)) + unapplied, {'applied': applied, 'pct': pct, 'unapplied': unapplied}


def stat_attack(main, sub, att, damage, final_damage, constant=1.0):
    return constant * (4 * main + sub) * att * 0.01 * (1 + damage / 100) * (1 + final_damage / 100)


def reproduce(ledger, classify=None):
    """스탯창 값과 출처 합산을 비교한다. 오차가 곧 '모르는 출처'의 크기다."""
    final = ledger.final
    main = main_stat(final)
    sub = SUB[main]
    power = 'MATT' if main == 'INT' else 'ATT'
    rows = []
    for stat in (main, sub, power):
        label = {'ATT': '공격력', 'MATT': '마력'}.get(stat, stat)
        actual = num(final.get(label))
        value, parts = predict(ledger, stat, classify)
        rows.append({'stat': label, 'actual': actual, 'predicted': value, 'gap': actual - value,
                     'gap_pct': round((actual - value) / actual * 100, 2) if actual else None, **parts})
    f = lambda k: float(str(final.get(k) or 0).replace(',', ''))
    base = stat_attack(f(main), f(sub), f({'ATT': '공격력', 'MATT': '마력'}[power]), f('데미지'), f('최종 데미지'))
    constant = f('최대 스탯공격력') / base if base else None
    return {'main': main, 'sub': sub, 'power': power, 'rows': rows,
            'weapon_constant': round(constant, 4) if constant else None,
            'mastery': round(f('최소 스탯공격력') / f('최대 스탯공격력'), 4) if f('최대 스탯공격력') else None}


def calibrate(ledger, classify=None):
    """설명되지 않는 차이를 두 방식으로 메운다. 교체 시뮬레이션은 두 방식의 결과를 범위로 보여 준다.

    'flat': 모르는 출처가 % 적용 고정치라고 본다. 'pct': 모르는 출처가 %라고 본다.
    """
    report = reproduce(ledger, classify)
    out = {}
    for row in report['rows']:
        key = {'공격력': 'ATT', '마력': 'MATT'}.get(row['stat'], row['stat'])
        base = 1 + row['pct'] / 100
        out[key] = {'flat': {'applied': row['applied'] + row['gap'] / base, 'pct': row['pct'], 'unapplied': row['unapplied']},
                    'pct': {'applied': row['applied'],
                            'pct': row['pct'] + (row['gap'] / row['applied'] * 100 if row['applied'] else 0),
                            'unapplied': row['unapplied']}}
    return report, out


def value(model, d_applied=0, d_pct=0):
    return (model['applied'] + d_applied) * (1 + (model['pct'] + d_pct) / 100) + model['unapplied']


def item_delta(level, old_item, new_item):
    """장비 한 개를 바꿀 때 스탯별 (% 적용 고정치 변화, % 변화). 세트 효과 변화는 따로 계산해야 한다(아직 안 함)."""
    before, after = Ledger(), Ledger()
    if old_item:
        item_sources(before, old_item, level)
    if new_item:
        item_sources(after, new_item, level)
    out = {}
    for stat in (*STATS, 'ATT', 'MATT', 'DAMAGE'):
        out[stat] = (after.total(stat, 'flat') - before.total(stat, 'flat'),
                     after.total(stat, 'pct') - before.total(stat, 'pct'))
    return out


def swap(ledger, old_item, new_item, classify=None):
    """한 부위 교체 → 최대 스탯공격력 변화율(%) 범위. 보스 공격력·방무는 스탯공격력에 들어가지 않아 따로 적는다."""
    report, models = calibrate(ledger, classify)
    delta = item_delta(ledger.level, old_item, new_item)
    final = ledger.final
    f = lambda k: float(str(final.get(k) or 0).replace(',', ''))
    main, sub, power = report['main'], report['sub'], report['power']
    before = stat_attack(f(main), f(sub), f({'ATT': '공격력', 'MATT': '마력'}[power]), f('데미지'), f('최종 데미지'))
    results = {}
    for way in ('flat', 'pct'):
        new = {k: value(models[k][way], *delta[k]) for k in (main, sub, power)}
        after = stat_attack(new[main], new[sub], new[power], f('데미지') + delta['DAMAGE'][1], f('최종 데미지'))
        results[way] = {'change_pct': round((after / before - 1) * 100, 3),
                        'stats': {k: round(new[k] - value(models[k][way])) for k in new}}
    low, high = sorted(r['change_pct'] for r in results.values())
    return {'range': [low, high], 'by_assumption': results, 'delta': delta,
            'note': '세트 효과 변화·보스 데미지·방어율 무시는 이 값에 들어 있지 않다.'}
