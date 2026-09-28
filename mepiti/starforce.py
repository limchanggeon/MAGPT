"""스타포스 강화 기대값.

확률표·비용식·복구 비용표는 오픈소스 계산기 mesulive에서 가져왔다.
  https://github.com/kurateh/mesulive (src/entities/starforce)
**넥슨이 직접 공시한 표를 이 앱이 검증한 것이 아니다.** 커뮤니티 구현을 옮긴 값이므로
결과에 출처를 함께 표시하고, 공식 확률 공시와 대조하기 전에는 확정으로 쓰지 않는다.

확률표에는 이미 스타캐치(x1.05)가 반영되어 있다. 0성 0.95 x 1.05 = 0.9975 처럼 맞는다.

기대값은 몬테카를로가 아니라 연립방정식으로 정확히 푼다. 같은 입력이면 항상 같은 값이 나와야
사용자가 결과를 신뢰하고 비교할 수 있기 때문이다.
"""
from .core import AppError

# [성공, 유지, 파괴] — 0성부터 29성까지. 스타캐치 적용 상태.
PROB_TABLE = [
    [0.9975, 0.0025, 0], [0.945, 0.055, 0], [0.8925, 0.1075, 0], [0.8925, 0.1075, 0],
    [0.84, 0.16, 0], [0.7875, 0.2125, 0], [0.735, 0.265, 0], [0.6825, 0.3175, 0],
    [0.63, 0.37, 0], [0.5775, 0.4225, 0], [0.525, 0.475, 0], [0.4725, 0.5275, 0],
    [0.42, 0.58, 0], [0.3675, 0.6325, 0], [0.315, 0.685, 0],
    [0.315, 0.66445, 0.02055], [0.315, 0.66445, 0.02055],
    [0.1575, 0.7751, 0.0674], [0.1575, 0.7751, 0.0674], [0.1575, 0.75825, 0.08425],
    [0.315, 0.58225, 0.10275], [0.1575, 0.716125, 0.126375], [0.1575, 0.674, 0.1685],
    [0.105, 0.716, 0.179], [0.105, 0.716, 0.179], [0.105, 0.716, 0.179],
    [0.0735, 0.7416, 0.1853], [0.0525, 0.758, 0.1895], [0.0315, 0.7748, 0.1937],
    [0.0105, 0.7916, 0.1979],
]
DESTROY_FALLBACK_STAR = 12      # 파괴되면 12성으로 떨어진다.


def normalised_table():
    """행 합이 1이 되도록 맞춘 확률표.

    원본 표의 26성 행은 합이 1.0004로 어긋나 있다(0.0735+0.7416+0.1853).
    그대로 두면 기대값이 미세하게 틀어지므로 유지 확률에서 차이를 덜어 맞춘다.
    원본을 고친 것이 아니라 계산에 쓰기 위해 보정한 것이며, 어긋난 행은 결과에 함께 알린다.
    """
    table, adjusted = [], []
    for star, row in enumerate(PROB_TABLE):
        total = sum(row)
        if abs(total - 1) > 1e-9:
            adjusted.append({'star': star, 'sum': round(total, 6)})
            row = [row[0], row[1] - (total - 1), row[2]]
        table.append(list(row))
    return table, adjusted
SOURCE = {'name': 'mesulive', 'url': 'https://github.com/kurateh/mesulive',
          'official': False, 'star_catch': True}

# 이벤트. mesulive의 eventSchema를 그대로 옮겼다.
# discount: 모든 성의 시도 비용에 곱하는 할인, restore_discount: 흔적 복구 비용 중 메소 부분 할인.
def _event(destroy=1.0, guaranteed=(), discount=0.0, one_plus_one=False, restore_discount=0.0):
    return {'destroy': destroy, 'guaranteed': guaranteed, 'discount': discount,
            'one_plus_one': one_plus_one, 'restore_discount': restore_discount}


EVENTS = {
    '없음': _event(),
    '10성 이하 1+1': _event(one_plus_one=True),
    '30% 할인': _event(discount=0.3),
    '흔적 복구 비용 20% 할인': _event(restore_discount=0.2),
    '5/10/15성 100%': _event(guaranteed=(5, 10, 15)),
    '21성 이하 파괴 30% 감소': _event(destroy=0.7),
    '샤타포스': _event(destroy=0.7, discount=0.3),
    '샤타포스(+흔적 복구 비용 20% 할인)': _event(destroy=0.7, discount=0.3, restore_discount=0.2),
    '샤타포스(15 16 포함)': _event(destroy=0.7, guaranteed=(5, 10, 15), discount=0.3),
}
DESTROY_REDUCTION_MAX_STAR = 22   # 파괴 확률 감소는 21성 이하(표 인덱스 0~21)에만 붙는다.
# 안전모드(파괴방지)는 15·16·17성 시도에서만 고를 수 있다(mesulive safeGuardRecordAtom).
SAFEGUARD_STARS = (15, 16, 17)

# 할인. 여러 개를 함께 쓰면 비율을 더하고, 16성 이하 시도에만 붙는다(mesulive: index < 17).
# 이벤트 할인은 그 위에 곱한다. 예: 16성 MVP 다이아+샤타포스 = 기본 x 0.9 x 0.7.
DISCOUNTS = {'MVP 실버': 0.03, 'MVP 골드': 0.05, 'MVP 다이아': 0.10, 'PC방': 0.05}
DISCOUNT_MAX_STAR = 17
RESTORE_MAX_STAR = 22             # 23성 이상에서 파괴되면 22성으로 복구한다.

# 흔적 복구: 레벨별 (성 -> [필요 스페어 개수, 복구 비용(억)])
RESTORE_TABLE = {
    130: {15:[1,1.19],16:[1,2.87],17:[1,4.85],18:[1,11.03],19:[2,18.27]},
    135: {15:[1,1.33],16:[1,3.21],17:[1,5.42],18:[1,12.31],19:[2,20.43]},
    140: {15:[1,1.48],16:[1,3.58],17:[1,6.05],18:[1,13.74],19:[2,22.79],20:[2,40.15],21:[3,50.45],22:[4,82.9]},
    145: {15:[1,1.65],16:[1,3.98],17:[1,6.71],18:[1,15.28],19:[2,25.4],20:[2,44.5],21:[3,56.05],22:[4,92.25]},
    150: {15:[1,1.83],16:[1,4.41],17:[1,7.45],18:[1,16.89],19:[2,28.03],20:[2,49.44],21:[3,62.24],22:[4,101.79]},
    160: {15:[1,2.22],16:[1,5.35],17:[1,9.03],18:[1,20.5],19:[2,34.02],20:[2,59.93],21:[3,75.31],22:[4,123.74]},
    200: {15:[1,4.33],16:[1,10.44],17:[1,17.64],18:[1,40.05],19:[2,66.44],20:[2,117.06],21:[3,147.09],22:[4,241.68]},
    250: {15:[1,8.46],16:[1,20.39],17:[1,34.46],18:[1,78.21],19:[2,129.77],20:[2,228.63],21:[3,287.28],22:[4,472.04]},
}


def restore_cost(level, star, spare_cost, meso_discount=0.0):
    """흔적 복구로 파괴 직전 성으로 되돌릴 때 드는 총비용. 불가능하면 None."""
    row = RESTORE_TABLE.get(int(level), {}).get(int(star))
    if not row:
        return None
    count, meso = row
    if count <= 0 or meso <= 0:
        return None
    return spare_cost * count + round(meso * 100_000_000 * (1 - meso_discount))


# 10성 이상 구간의 비용 제수. 없는 성은 200을 쓴다.
COST_DIVISOR = {10: 571, 11: 314, 12: 214, 13: 157, 14: 107, 17: 150, 18: 70, 19: 45, 21: 125}


def reachable_star(level):
    if level < 95:
        return 5
    if level <= 107:
        return 10
    if level <= 127:
        return 15
    if level <= 137:
        return 20
    return 30


def attempt_costs(level):
    """성별 1회 시도 비용(메소)."""
    costs = []
    for star in range(30):
        if star <= 9:
            costs.append(round((1000 + level ** 3 * (star + 1) / 36) / 100) * 100)
        else:
            base = level ** 3 * (star + 1) ** 2.7
            costs.append(1000 + round(base / COST_DIVISOR.get(star, 200) / 100) * 100)
    return costs


def solve(matrix, vector):
    """작은 연립방정식을 가우스 소거로 푼다. 외부 수치 라이브러리를 들이지 않으려는 것이다."""
    size = len(vector)
    rows = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) < 1e-12:
            raise AppError('기대값을 계산할 수 없는 조건입니다. 현재·목표 성을 확인해 주세요.')
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for r in range(size):
            if r == column:
                continue
            factor = rows[r][column] / rows[column][column]
            if factor:
                for c in range(column, size + 1):
                    rows[r][c] -= factor * rows[column][c]
    return [rows[i][size] / rows[i][i] for i in range(size)]


def expected(data):
    """현재 성에서 목표 성까지의 기대 비용·시도·파괴 횟수.

    `spare_cost`는 파괴 시 새로 마련하는 같은 장비의 값(노작값)이다. 모르면 0으로 두고
    파괴 횟수만 본다. 비용을 0으로 둔 채 총비용을 '이만큼이면 된다'로 읽으면 안 된다.
    """
    def integer(key, low, high, default=None):
        value = data.get(key, default)
        if value is None or value == '':
            raise AppError(f'{key} 값을 입력해 주세요.')
        try:
            value = int(float(str(value).replace(',', '')))
        except (TypeError, ValueError):
            raise AppError(f'{key} 값은 숫자여야 합니다.')
        if not low <= value <= high:
            raise AppError(f'{key} 값은 {low}~{high} 범위여야 합니다.')
        return value

    level = integer('level', 1, 300)
    current = integer('current_star', 0, 29)
    target = integer('target_star', 1, 30)
    try:
        spare_cost = float(str(data.get('spare_cost') or 0).replace(',', ''))
    except (TypeError, ValueError):
        raise AppError('spare_cost 값은 숫자여야 합니다.')
    if spare_cost < 0:
        raise AppError('스페어 비용은 0 이상이어야 합니다.')
    limit = reachable_star(level)
    if target > limit:
        raise AppError(f'{level}레벨 장비는 {limit}성까지만 강화할 수 있습니다.')
    if target <= current:
        raise AppError('목표 성은 현재 성보다 높아야 합니다.')

    event_name = str(data.get('event') or '없음')
    if event_name not in EVENTS:
        raise AppError('지원하지 않는 이벤트입니다. ' + ', '.join(EVENTS))
    event = EVENTS[event_name]
    picked = [d for d in (data.get('discounts') or []) if d in DISCOUNTS]
    discount = sum(DISCOUNTS[d] for d in picked)
    use_restore = bool(data.get('use_restore'))
    meso_discount = max(event['restore_discount'], 0.2 if data.get('restore_discount') else 0.0)

    base_costs = attempt_costs(level)
    costs = [round(c * (1 - discount if star < DISCOUNT_MAX_STAR else 1) * (1 - event['discount']))
             for star, c in enumerate(base_costs)]
    table, adjusted = normalised_table()
    requested = set(int(s) for s in (data.get('safeguard') or []) if str(s).isdigit())
    safeguard = requested & set(SAFEGUARD_STARS)
    ignored_safeguard = sorted(requested - safeguard)
    # 이벤트를 확률표에 반영한다. 파괴 감소분은 유지 쪽으로 옮긴다.
    for star, row in enumerate(table):
        if event['destroy'] != 1.0 and star < DESTROY_REDUCTION_MAX_STAR and row[2]:
            cut = row[2] * (1 - event['destroy'])
            row[1] += cut
            row[2] -= cut
        if star in event['guaranteed']:
            table[star] = [1.0, 0.0, 0.0]

    # E[s] = 비용 + p유지*E[s] + p성공*E[s+1] + p파괴*(스페어 + E[12])
    size = target
    matrix = [[0.0] * size for _ in range(size)]
    vector = [0.0] * size
    attempts = [[0.0] * size for _ in range(size)]
    attempt_vec = [0.0] * size
    destroys = [[0.0] * size for _ in range(size)]
    destroy_vec = [0.0] * size
    restore_used = []
    safeguard_used = []
    for star in range(size):
        success, maintain, destroy = table[star]
        attempt = costs[star]
        # 안전모드는 파괴를 막는 대신 할인 없는 기본 비용의 2배를 더 받는다.
        # 100% 성공 구간(5/10/15성 이벤트)에서는 추가 비용이 없다(mesulive isDecided).
        if star in safeguard and success < 1.0:
            attempt += base_costs[star] * 2
            safeguard_used.append(star)
            maintain, destroy = maintain + destroy, 0.0
        # 1+1 이벤트는 10성 이하에서 성공 시 두 칸 오른다.
        jump = 2 if (event['one_plus_one'] and star <= 10) else 1
        # 흔적 복구를 쓰면 파괴돼도 그 성으로 돌아온다. 아니면 12성으로 떨어진다.
        back, penalty = DESTROY_FALLBACK_STAR, spare_cost
        if destroy and use_restore:
            to = min(star, RESTORE_MAX_STAR)
            recovered = restore_cost(level, to, spare_cost, meso_discount)
            if recovered is not None:
                back, penalty = to, recovered
                restore_used.append(to)
        for row, rhs, own_cost, own_destroy in ((matrix, vector, attempt, 0.0),
                                                (attempts, attempt_vec, 1.0, 0.0),
                                                (destroys, destroy_vec, 0.0, 1.0)):
            row[star][star] += 1.0 - maintain
            landing = min(star + jump, size)
            if landing < size:
                row[star][landing] -= success
            if destroy and back < size:
                row[star][back] -= destroy
            rhs[star] = own_cost + destroy * (penalty if row is matrix else own_destroy)

    cost = solve(matrix, vector)[current]
    tries = solve(attempts, attempt_vec)[current]
    broken = solve(destroys, destroy_vec)[current]
    return {
        'level': level, 'current_star': current, 'target_star': target,
        'spare_cost': spare_cost, 'spare_cost_known': bool(spare_cost),
        'adjusted_rows': adjusted, 'event': event_name, 'discounts': picked,
        'discount_ratio': round(discount, 4), 'event_discount': event['discount'],
        'use_restore': use_restore, 'restore_meso_discount': meso_discount,
        'restore_stars': sorted(set(restore_used)),
        'expected_cost': round(cost),
        'expected_attempts': round(tries, 2),
        'expected_destroys': round(broken, 3),
        'attempt_cost_at_current': costs[current],
        'safeguard': sorted(safeguard_used),
        'safeguard_ignored': ignored_safeguard,
        'source': dict(SOURCE),
        'assumptions': [
            '확률표·비용식은 오픈소스 계산기 mesulive에서 가져왔습니다. 넥슨 공시와 이 앱이 대조한 값이 아닙니다.',
            '확률표에는 스타캐치(성공확률 x1.05)가 이미 반영되어 있습니다.',
            f'흔적 복구를 쓰지 않은 파괴는 {DESTROY_FALLBACK_STAR}성으로 떨어지고 스페어 장비 1개 값을 치르는 것으로 계산했습니다.',
            ('**스페어 비용을 모릅니다.** 파괴되면 어느 쪽으로 처리하든 같은 장비가 한 개 이상 필요하므로, '
             '아래 총비용에는 파괴 손실이 빠져 있습니다. 노작값을 알려주면 다시 계산합니다.'
             if not spare_cost else f'파괴 1회당 스페어 장비 값 {spare_cost:,.0f} 메소로 계산했습니다.'),
            (f'이벤트: {event_name}' if event_name != '없음' else '이벤트를 적용하지 않은 기본 비용입니다.'),
            (f"안전모드 적용 구간 {', '.join(str(x)+'성' for x in safeguard_used)}. "
             '안전모드는 파괴를 막는 대신 할인 없는 기본 비용의 2배를 추가로 냅니다.'
             if safeguard_used else '안전모드는 쓰지 않는 것으로 계산했습니다.'),
            (f"{', '.join(picked)} 할인 {round(discount*100,1)}%는 16성 이하 시도에만 적용했습니다."
             if discount else 'MVP·PC방 할인 없음.'),
            (f"이벤트 비용 할인 {round(event['discount']*100)}%를 모든 시도에 곱해 적용했습니다."
             if event['discount'] else '이벤트 비용 할인 없음.'),
            (f"흔적 복구를 쓰는 것으로 계산했습니다(적용 구간 {', '.join(str(x)+'성' for x in sorted(set(restore_used)))})."
             if restore_used else '흔적 복구는 쓰지 않는 것으로 계산했습니다. 파괴 시 12성으로 떨어집니다.'),
        ] + ([f"안전모드는 15~17성에서만 쓸 수 있어 {', '.join(str(x)+'성' for x in ignored_safeguard)} 요청은 반영하지 않았습니다."]
             if ignored_safeguard else []) + ([f"원본 확률표의 {r['star']}성 행 합이 {r['sum']}이라 유지 확률에서 차이를 덜어 맞췄습니다."
              for r in adjusted]),
    }
