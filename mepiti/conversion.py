"""주스탯 환산 계산.

커뮤니티 계산기 "주스탯 환산"(제작 윤두꺼/스카니아, 설명 새틀라이트)의 계산 절차를 옮긴 것이다.
**넥슨 공식 수치가 아니다.** 직업 패시브 값과 회귀 계수는 제작자가 측정·적합한 값이며
패치로 달라질 수 있다. 결과는 참고용이고, 게임이 보장하는 값처럼 제시하지 않는다.

절차는 원본 계산기와 같다.
  1. 메용 적용/미적용 스탯 차이에서 스탯퍼를 역산한다.
  2. 심볼·하이퍼·유니온 스탯을 뺀 뒤 스탯퍼로 나눠 순스탯을 구한다.
  3. 직업 패시브 차이를 빼고 아델 패시브를 더해 아델 기준으로 옮긴다.
  4. 아델의 샤드 한 줄 데미지를 계산한다.
  5. 그 데미지를 제작자의 회귀식으로 주스탯에 되돌린다.
"""
import math

from .core import AppError
from .jobdata import (BENCHMARKS, COOLDOWN, CORRECTED_PASSIVE, GRADES, GRADE_FLOOR,
                      QUAD_A, QUAD_B, QUAD_C, RAW_PASSIVE, REFERENCE_JOB, SCORE_BASE, SOURCE)

DOUBLE_SUB_STAT_JOBS = ('듀얼블레이드', '섀도어', '카데나')
PALADIN_RATE, DEFAULT_RATE = 0.16, 0.15
MOB_DEFENSE = 50.0
SHARD_PIERCE = 20.0      # 샤드가 추가로 무시하는 방어율
LEVEL_GAP = 1.2
SHARD_RATE = 4.5
CORE_RATE = 2.2
SUB_STAT_CONSTANT = 2.5
ADELE_MASTERY = 0.9


def xround(value, digits=0):
    """엑셀 ROUND. 파이썬 round는 짝수 쪽으로 붙으므로 사사오입을 직접 구현한다."""
    factor = 10 ** digits
    scaled = value * factor
    return math.floor(scaled + 0.5) / factor if scaled >= 0 else math.ceil(scaled - 0.5) / factor


def xtrunc(value, digits=0):
    """엑셀 ROUNDDOWN. 0 방향으로 버린다."""
    factor = 10 ** digits
    scaled = value * factor
    return (math.floor(scaled) if scaled >= 0 else math.ceil(scaled)) / factor


def xceil(value, digits=0):
    """엑셀 ROUNDUP. 0에서 먼 쪽으로 올린다."""
    factor = 10 ** digits
    scaled = value * factor
    return (math.ceil(scaled) if scaled >= 0 else math.floor(scaled)) / factor


def number(data, key, low, high, required=True, default=0.0):
    value = data.get(key)
    if value is None or value == '':
        if required:
            raise AppError(f'{key} 값을 입력해 주세요.')
        return float(default)
    try:
        value = float(str(value).replace(',', ''))
    except (TypeError, ValueError):
        raise AppError(f'{key} 값은 숫자여야 합니다.')
    if not math.isfinite(value) or not low <= value <= high:
        raise AppError(f'{key} 값은 {low:g} 이상 {high:g} 이하여야 합니다.')
    return value


def stat_for_score(score):
    """샤드뎀 점수 -> 환산 주스탯."""
    return xround(QUAD_A * score * score + QUAD_B * score + QUAD_C, 0)


def score_for_stat(stat):
    """환산 주스탯 -> 그 스탯이 내야 하는 샤드뎀 점수. 이차식의 근의 공식."""
    discriminant = QUAD_B * QUAD_B - 4 * QUAD_A * (QUAD_C - stat)
    if discriminant < 0:
        return None
    root = (-QUAD_B + math.sqrt(discriminant)) / (2 * QUAD_A)
    return 10 ** (root / 100) * SCORE_BASE


def convert(data):
    job = str(data.get('job', '')).strip()
    if job not in RAW_PASSIVE:
        raise AppError('지원하는 직업을 선택해 주세요.')
    cooldown = str(data.get('cooldown', '노쿨감')).strip()
    if cooldown not in COOLDOWN:
        raise AppError('쿨타임 감소 구간을 선택해 주세요.')
    mine, adele = CORRECTED_PASSIVE[job], RAW_PASSIVE[REFERENCE_JOB]
    weapon_constant = RAW_PASSIVE[job]['무기상수']

    level = number(data, 'level', 200, 300)
    buffed = number(data, 'buffed_stat', 1, 2_000_000)       # 메용 적용
    plain = number(data, 'plain_stat', 1, 2_000_000)         # 메용 미적용
    sub_stat = number(data, 'sub_stat', 0, 2_000_000)
    sub_stat2 = number(data, 'sub_stat2', 0, 2_000_000, required=False)
    stat_attack = number(data, 'stat_attack', 1, 1e12)       # 스공(뒷스공)
    damage = number(data, 'damage', 0, 10_000)
    boss_damage = number(data, 'boss_damage', 0, 10_000)
    defense_ignore = number(data, 'defense_ignore', 0, 99.9999)
    critical_damage = number(data, 'critical_damage', 0, 1_000)
    item_attack_percent = number(data, 'item_attack_percent', 0, 10_000)
    arcane = number(data, 'arcane_stat', 0, 1_000_000, required=False)
    authentic = number(data, 'authentic_stat', 0, 1_000_000, required=False)
    hyper = number(data, 'hyper_stat', 0, 1_000_000, required=False)
    union = number(data, 'union_stat', 0, 1_000_000, required=False)
    boss_ability = bool(data.get('boss_ability'))

    if buffed <= plain:
        raise AppError('메용 적용 스탯이 미적용 스탯보다 커야 합니다. 두 값을 다시 확인해 주세요.')

    # 1. 스탯퍼 역산
    ap = 18 + level * 5
    pure_gain = xtrunc(ap * (PALADIN_RATE if job == '팔라딘' else DEFAULT_RATE))
    stat_percent = xround((buffed - plain) / pure_gain * 100 - 100, 0) / 100

    # 2. 순스탯
    flat_stat = arcane + authentic + hyper + union      # 스탯퍼가 붙지 않는 스탯
    if plain - flat_stat <= 0:
        raise AppError('심볼·하이퍼·유니온 스탯의 합이 메용 미적용 스탯보다 큽니다. 입력을 확인해 주세요.')
    pure_stat = xceil((plain - flat_stat) / (1 + stat_percent))

    # 3. 공격력 역산
    total_sub_stat = sub_stat + (sub_stat2 if job in DOUBLE_SUB_STAT_JOBS else 0)
    attack_percent = item_attack_percent + RAW_PASSIVE[job]['공퍼']
    divisor = ((buffed * 4 + total_sub_stat) * 0.01 * weapon_constant
               * (1 + attack_percent / 100) * (1 + damage / 100) * (1 + mine['최종뎀'] / 100))
    attack = xceil(stat_attack / divisor)

    # 4. 아델 기준으로 보정
    adjusted = {
        'pure_stat': pure_stat - mine['순스탯'] + adele['순스탯'],
        'attack': attack - mine['직업공격력'] + adele['직업공격력'],
        'damage': damage - mine['뎀지'] + adele['뎀지'],
        'critical_damage': critical_damage - mine['크뎀'] + adele['크뎀'],
    }
    adjusted['boss_damage'] = (boss_damage - mine['보공'] + adele['보공']
                               - (10 if boss_ability else 0))
    adjusted['sub_stat'] = total_sub_stat + (adele['부스탯'] - mine['부스탯']) * SUB_STAT_CONSTANT
    adjusted['stat_percent'] = stat_percent + COOLDOWN[cooldown]
    adjusted['attack_percent'] = item_attack_percent + adele['공퍼']

    # 방어율 무시는 곱연산이라 내 직업 패시브를 걷어낸 뒤 아델 것을 다시 곱한다.
    bare_defense = (defense_ignore - mine['방무']) / (1 - mine['방무'] / 100)
    adele_defense = bare_defense + (100 - bare_defense) * adele['방무'] / 100
    shard_defense = adele_defense + (100 - adele_defense) * SHARD_PIERCE / 100

    # 5. 아델의 스탯을 다시 조립한다.
    rebuilt_plain = xtrunc(adjusted['pure_stat'] * (1 + adjusted['stat_percent'])) + flat_stat
    rebuilt_buffed = rebuilt_plain + xround(
        xtrunc(ap * DEFAULT_RATE) * (1 + adjusted['stat_percent']))

    # 6. 샤드 한 줄 데미지
    main_stat = rebuilt_buffed + xtrunc(30 * (1 + adjusted['stat_percent']))
    off_stat = adjusted['sub_stat'] + xtrunc(30 * SUB_STAT_CONSTANT)
    shard = xtrunc(
        (main_stat * 4 + off_stat) / 100
        * (adjusted['attack'] + 180)
        * (1 + (adjusted['attack_percent'] + 4) / 100)
        * (1 + (adjusted['damage'] + adjusted['boss_damage'] + 6 + 90) / 100)
        * (1 + adele['최종뎀'] / 100)
        * ((100 - (MOB_DEFENSE - MOB_DEFENSE * shard_defense / 100)) / 100)
        * ((135 + adjusted['critical_damage'] + 30) / 100)
        * LEVEL_GAP * SHARD_RATE * weapon_constant_of_adele()
        * ((ADELE_MASTERY + 1) / 2)
        * CORE_RATE / 10)

    # 7. 데미지를 주스탯으로 되돌린다.
    score = xtrunc(shard / 10_000_000, 2)
    if score <= 0:
        raise AppError('입력값으로는 환산 주스탯을 계산할 수 없습니다. 스공과 스탯을 확인해 주세요.')
    exponent = xtrunc(math.log10(score / SCORE_BASE) * 100, 2)
    if exponent <= 0:
        raise AppError('입력값이 계산기의 적용 범위를 벗어납니다. 스공과 스탯을 확인해 주세요.')
    converted = stat_for_score(exponent)

    return {
        'converted_stat': converted,
        'score': score,
        'shard_damage': shard,
        'grade': grade_for(converted - buffed),
        'stat_sheet_ratio': ratio_against(score, buffed),
        'benchmarks': benchmark_pair(converted, score),
        'derived': {
            'ap': ap, 'stat_percent': stat_percent, 'pure_stat': pure_stat,
            'attack': attack, 'total_sub_stat': total_sub_stat,
            'adjusted_stat_percent': adjusted['stat_percent'],
            'shard_defense_ignore': round(shard_defense, 4),
        },
        'source': dict(SOURCE),
        'assumptions': [
            '넥슨 공식 수치가 아닙니다. 커뮤니티 계산기 "주스탯 환산"의 절차와 기준표를 그대로 옮긴 결과입니다.',
            f'제작 {SOURCE["author"]} · 설명 {SOURCE["explainer"]}. 직업 패시브와 회귀 계수는 제작자 측정값이며 패치로 달라질 수 있습니다.',
            '스탯창 조건: 시드링 착용, 노도핑, 레조 스택 없음, 메용 사용, 쓸만한 샤프 아이즈 적용 기준입니다.',
            f'{REFERENCE_JOB} 기준으로 직업 패시브를 보정한 뒤 샤드 한 줄 데미지로 환산했습니다.',
        ],
    }


def weapon_constant_of_adele():
    return RAW_PASSIVE[REFERENCE_JOB]['무기상수']


def grade_for(gap):
    for threshold, name in GRADES:
        if gap > threshold:
            return {'name': name, 'gap': int(gap)}
    return {'name': GRADE_FLOOR, 'gap': int(gap)}


def ratio_against(score, stat_sheet_stat):
    expected = score_for_stat(stat_sheet_stat)
    if not expected:
        return None
    return xround(score / expected - 1, 4)


def benchmark_pair(converted, score):
    """원본과 같이 환산 주스탯이 속한 구간과 바로 윗 구간을 함께 돌려준다."""
    index = None
    for i, (_, _, stat) in enumerate(BENCHMARKS):
        if converted >= stat:
            index = i
    result = []
    for position in (index, None if index is None else index + 1):
        if position is None or position >= len(BENCHMARKS):
            continue
        name, benchmark_score, benchmark_stat = BENCHMARKS[position]
        result.append({'name': name, 'stat': round(benchmark_stat),
                       'ratio': xround(score / benchmark_score - 1, 4)})
    return result


def jobs():
    return sorted(RAW_PASSIVE)


def cooldowns():
    return list(COOLDOWN)
