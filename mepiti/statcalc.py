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
    ledger.sets = {}
    for s in raw['character/set-effect'].get('set_effect') or []:
        ledger.sets[s['set_name']] = {'count': num(s.get('total_set_count')), 'full': s.get('set_option_full') or [],
                                      'verified': False, 'lucky': False}
        for o in s.get('set_effect_info') or []:
            for k, v, pct in split_stats(o.get('set_option') or ''):
                ledger.add(k, '세트', f"{s['set_name']} {o['set_count']}세트", v, 'pct' if pct else 'flat')
    ledger.items = equipment.get('item_equipment') or []
    verify_sets(ledger, ledger.items)
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


# 보스 장신구 계열 세트는 이름에 세트 이름이 없다. 소속을 목록으로 판별하고, 캐릭터의 실제 세트 수(API)와 맞는지 대조해서만 쓴다.
SET_MEMBERS = {
    '보스 장신구 세트': ('응축된 힘의 결정석', '아쿠아틱 레터 눈장식', '블랙빈 마크', '파풀라투스 마크', '데아 시두스 이어링',
                    '지옥의 불꽃', '골든 클로버 벨트', '실버블라썸 링', '고귀한 이피아의 반지', '가디언 엔젤 링',
                    '혼테일의 목걸이', '카오스 혼테일의 목걸이', '매커네이터 펜던트', '도미네이터 펜던트', '핑크빛 성배',
                    '크리스탈 웬투스 뱃지', '로얄 블랙메탈 숄더'),
    '칠흑의 보스 세트': ('루즈 컨트롤 머신 마크', '마력이 깃든 안대', '몽환의 벨트', '고통의 근원', '창세의 뱃지',
                    '커맨더 포스 이어링', '거대한 공포', '미트라의 분노', '저주받은 적의 마도서', '저주받은 청의 마도서',
                    '저주받은 녹의 마도서', '저주받은 황의 마도서'),
    '여명의 보스 세트': ('트와일라이트 마크', '에스텔라 이어링', '데이브레이크 펜던트', '여명의 가디언 엔젤 링'),
    '루타비스 세트': ('하이네스', '이글아이', '트릭스터'),
}
LUCKY = ('제네시스',)        # 럭키 아이템: 3세트 이상 효과가 있는 방어구 세트마다 1개로 센다(API 세트 수로 대조)


def is_lucky(item_name):
    return (item_name or '').startswith(LUCKY)


def set_key(set_name):
    """'에테르넬 세트(전사)' → '에테르넬', '도전자의 장비 세트(전사)' → '도전자의'."""
    return re.split(r'\s*(?:장비\s*)?세트', set_name)[0].strip()


def set_of(item_name, set_names):
    """장비가 속한 세트(캐릭터가 아는 세트 이름 중에서). 모르면 None."""
    name = item_name or ''
    for set_name in set_names:
        members = next((v for k, v in SET_MEMBERS.items() if set_name.startswith(k)), None)
        if members:
            if any(name.startswith(m) for m in members):
                return set_name
            continue
        key = set_key(set_name)
        if key and name.startswith(key):
            return set_name
    return None


def verify_sets(ledger, items):
    """규칙으로 센 세트 수가 API 세트 수와 같으면(럭키 아이템 1개 포함 가능) 그 세트를 믿는다."""
    names = list(ledger.sets)
    lucky = any(is_lucky(i.get('item_name')) for i in items)
    counts = {}
    for i in items:
        s = set_of(i.get('item_name'), names)
        if s:
            counts[s] = counts.get(s, 0) + 1
    for name, info in ledger.sets.items():
        mine = counts.get(name, 0)
        if mine == info['count']:
            info['verified'] = True
        elif lucky and mine + 1 == info['count']:
            info['verified'] = info['lucky'] = True


def set_bonus(full, count):
    """세트 단계표에서 count개일 때 받는 효과의 합: {키: (고정, %)}, 보스%, 방무 목록."""
    out = {'stats': {}, 'boss': 0, 'ied': []}
    for tier in full or []:
        if num(tier.get('set_count')) > count:
            continue
        text = tier.get('set_option') or ''
        for k, v, pct in split_stats(text):
            flat, per = out['stats'].get(k, (0, 0))
            out['stats'][k] = (flat + (0 if pct else v), per + (v if pct else 0))
        for m in re.finditer(r'보스 몬스터 데미지 \+(\d+)%', text):
            out['boss'] += int(m[1])
        for m in re.finditer(r'몬스터 방어율 무시 \+(\d+)%', text):
            out['ied'].append(int(m[1]))
    return out


def set_change(ledger, old_item, new_item):
    """한 부위 교체로 바뀌는 세트 효과. 판별 못 한 세트는 unknown에 이름을 남긴다."""
    sets = getattr(ledger, 'sets', {})
    names = list(sets)
    old_set = set_of((old_item or {}).get('item_name'), names)
    new_set = set_of((new_item or {}).get('item_name'), names)
    change = {'stats': {}, 'boss': 0, 'ied_add': [], 'ied_remove': [], 'notes': [], 'unknown': []}
    if is_lucky((old_item or {}).get('item_name')) != is_lucky((new_item or {}).get('item_name')):
        change['unknown'].append('럭키 아이템(제네시스 무기)을 바꾸면 여러 세트 수가 함께 바뀌는데, 이건 아직 계산하지 않았어요')
    if old_set == new_set:
        return change
    for set_name in (old_set, new_set):
        if set_name and not sets[set_name]['verified']:
            change['unknown'].append(f'{set_name}(착용 장비로 세트 수를 맞추지 못해 계산에서 뺐어요)')
    old_set = old_set if old_set and sets[old_set]['verified'] else None
    new_set = new_set if new_set and sets[new_set]['verified'] else None
    for set_name, step in ((old_set, -1), (new_set, +1)):
        if not set_name:
            continue
        info = sets[set_name]
        a, b = set_bonus(info['full'], info['count']), set_bonus(info['full'], info['count'] + step)
        for k in set(a['stats']) | set(b['stats']):
            fa, pa = a['stats'].get(k, (0, 0))
            fb, pb = b['stats'].get(k, (0, 0))
            f0, p0 = change['stats'].get(k, (0, 0))
            change['stats'][k] = (f0 + fb - fa, p0 + pb - pa)
        change['boss'] += b['boss'] - a['boss']
        extra = [x for x in b['ied']]
        for x in a['ied']:
            if x in extra:
                extra.remove(x)
            else:
                change['ied_remove'].append(x)
        change['ied_add'] += extra
        change['notes'].append(f"{set_name} {info['count']}→{info['count'] + step}세트")
    new_name = (new_item or {}).get('item_name') or ''
    if new_item and not new_set and re.search(r'에테르넬|아케인셰이드|앱솔랩스|도전자|루타비스|하이네스|이글아이|트릭스터', new_name):
        change['unknown'].append(new_name + '의 세트(지금 착용하지 않은 세트라 단계표를 모름)')
    return change


def extras(item):
    """장비 한 개의 보스%, 방무(곱연산 목록), 크리티컬 데미지%, 데미지%."""
    total = (item or {}).get('item_total_option') or {}
    out = {'boss': num(total.get('boss_damage')), 'ied': [num(total.get('ignore_monster_armor'))] if num(total.get('ignore_monster_armor')) else [],
           'crit': 0}
    for line in potential_lines(item or {}):
        m = re.match(r'보스 몬스터 공격 시 데미지 \+(\d+)%$', line)
        if m:
            out['boss'] += int(m[1])
        m = re.match(r'몬스터 방어율 무시 \+(\d+)%$', line)
        if m:
            out['ied'].append(int(m[1]))
        m = re.match(r'크리티컬 데미지 \+(\d+)%$', line)
        if m:
            out['crit'] += int(m[1])
    return out


def combine_ied(base, add=(), remove=()):
    """방어율 무시는 곱연산: 1 - (1 - 기존) × Π(1 - 추가) ÷ Π(1 - 제거)."""
    rest = 1 - base / 100
    for x in remove:
        rest /= max(1e-9, 1 - x / 100)
    for x in add:
        rest *= 1 - x / 100
    return (1 - rest) * 100


BOSS_DEFENSE = 300      # 보스 기준 비교에 쓰는 방어율(%). 검은 마법사·세렌 등 최상위 보스급.


def boss_score(main, sub, att, damage, boss, final_damage, crit_damage, ied, defense=BOSS_DEFENSE):
    """보스 기준 상대 딜 지표. 크리티컬은 항상 터진다고 보고 기본 크뎀 35%를 더한다."""
    guard = max(0.0, 1 - defense / 100 * (1 - ied / 100))
    return (4 * main + sub) * att * (1 + (damage + boss) / 100) * (1 + final_damage / 100) * (1.35 + crit_damage / 100) * guard


def item_delta(level, old_item, new_item, sets=None):
    """장비 한 개를 바꿀 때 스탯별 (% 적용 고정치 변화, % 변화). sets(set_change 결과)를 주면 세트 효과 변화도 더한다."""
    before, after = Ledger(), Ledger()
    if old_item:
        item_sources(before, old_item, level)
    if new_item:
        item_sources(after, new_item, level)
    out = {}
    for stat in (*STATS, 'ATT', 'MATT', 'DAMAGE'):
        out[stat] = (after.total(stat, 'flat') - before.total(stat, 'flat'),
                     after.total(stat, 'pct') - before.total(stat, 'pct'))
    for k, (flat, pct) in ((sets or {}).get('stats') or {}).items():
        for stat in (STATS if k == 'ALL' else (k,)):
            if stat in out:
                f0, p0 = out[stat]
                out[stat] = (f0 + flat, p0 + pct)
    return out


def swap(ledger, old_item, new_item, classify=None, defense=BOSS_DEFENSE):
    """한 부위 교체 → 최대 스탯공격력 변화율과 보스 기준 변화율(%)을 범위로. 세트 효과 변화 포함."""
    report, models = calibrate(ledger, classify)
    sets = set_change(ledger, old_item, new_item)
    delta = item_delta(ledger.level, old_item, new_item, sets)
    a, b = extras(old_item), extras(new_item)
    d_boss = b['boss'] - a['boss'] + sets['boss']
    d_crit = b['crit'] - a['crit']
    final = ledger.final
    f = lambda k: float(str(final.get(k) or 0).replace(',', ''))
    main, sub, power = report['main'], report['sub'], report['power']
    power_label = {'ATT': '공격력', 'MATT': '마력'}[power]
    ied_after = combine_ied(f('방어율 무시'), b['ied'] + sets['ied_add'], a['ied'] + sets['ied_remove'])
    before = stat_attack(f(main), f(sub), f(power_label), f('데미지'), f('최종 데미지'))
    before_boss = boss_score(f(main), f(sub), f(power_label), f('데미지'), f('보스 몬스터 데미지'), f('최종 데미지'),
                             f('크리티컬 데미지'), f('방어율 무시'), defense)
    results = {}
    for way in ('flat', 'pct'):
        new = {k: f({'ATT': '공격력', 'MATT': '마력'}.get(k, k)) + value(models[k][way], *delta[k]) - value(models[k][way])
               for k in (main, sub, power)}
        damage = f('데미지') + delta['DAMAGE'][1]
        after = stat_attack(new[main], new[sub], new[power], damage, f('최종 데미지'))
        after_boss = boss_score(new[main], new[sub], new[power], damage, f('보스 몬스터 데미지') + d_boss, f('최종 데미지'),
                                f('크리티컬 데미지') + d_crit, ied_after, defense)
        results[way] = {'change_pct': round((after / before - 1) * 100, 3),
                        'boss_change_pct': round((after_boss / before_boss - 1) * 100, 3) if before_boss else None,
                        'stats': {k: round(new[k] - f({'ATT': '공격력', 'MATT': '마력'}.get(k, k))) for k in new}}
    low, high = sorted(r['change_pct'] for r in results.values())
    blow, bhigh = sorted(r['boss_change_pct'] or 0 for r in results.values())
    return {'range': [low, high], 'boss_range': [blow, bhigh], 'by_assumption': results, 'delta': delta,
            'boss': d_boss, 'crit': d_crit, 'ied': [round(f('방어율 무시'), 2), round(ied_after, 2)], 'defense': defense,
            'sets': sets['notes'], 'unknown': sets['unknown'],
            'note': f'보스 기준은 방어율 {defense}% 보스, 크리티컬 항상 발동으로 본 상대값이다. 전투력·실제 딜 시간은 아니다.'}


CACHE = """
CREATE TABLE IF NOT EXISTS stat_raw(name TEXT PRIMARY KEY, day TEXT NOT NULL, data TEXT NOT NULL);
"""


def fetch(get, name, sleep=None):
    """스탯 출처 원본을 넥슨에서 받는다(약 22회). get(path, query)."""
    import time
    pause = sleep or time.sleep
    ocid = get('id', {'character_name': name}).get('ocid')
    raw = {}
    for path in PATHS:
        pause(0.3)
        raw[path] = get(path, {'ocid': ocid})
    raw['skills'] = []
    for grade in SKILL_GRADES:
        pause(0.3)
        try:
            raw['skills'] += get('character/skill', {'ocid': ocid, 'character_skill_grade': grade}).get('character_skill') or []
        except Exception:
            pass
    return raw


def load(store, get, name, refresh=False, fetch_missing=True, sleep=None):
    """캐릭터의 스탯 출처(Ledger). 하루(KST)에 한 번만 넥슨에서 받고 DB에 둔다. fetch_missing=False면 캐시만 본다."""
    import json
    from datetime import datetime
    from .core import KST
    day = datetime.now(KST).date().isoformat()
    with store.db() as db:
        db.executescript(CACHE)
    row = store.rows('SELECT day, data FROM stat_raw WHERE name=?', (name,))
    if row and row[0]['day'] == day and not refresh:
        return build(json.loads(row[0]['data']))
    if not fetch_missing:
        return None
    raw = fetch(get, name, sleep)
    with store.db() as db:
        db.execute('INSERT INTO stat_raw(name, day, data) VALUES(?,?,?) '
                   'ON CONFLICT(name) DO UPDATE SET day=excluded.day, data=excluded.data',
                   (name, day, json.dumps(raw, ensure_ascii=False)))
    return build(raw)
