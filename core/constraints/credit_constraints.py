"""Bước 2.6: thêm rule nhu cầu, đăng ký và biến công; không sửa nhóm A/B."""
from dataclasses import dataclass
from core.validation.credit_checks import (demand_requirements,request_map,target_credit,
                            check_work_credit,SHIFT_CODES,NON_SHIFT_CODES)

@dataclass
class CreditModel:
    total: dict
    excess: dict
    missing: dict
    spread: object
    deviation: object
    objective: object
    expected_total: int


def add_demand_constraints(ctx,data):
    demand = demand_requirements(data)
    for (d,s),n in demand.items():
        ctx.model.add(sum(ctx.x[i,d,s] for i in ctx.employee_ids)==n)
    return demand


def add_request_constraints(ctx,data):
    req = request_map(data)
    people = {e.id:e for e in data.employees}
    for (i,d),code in req.items():
        if code in SHIFT_CODES:
            if people[i].group == 'ADMIN':
                raise ValueError(f'ID {i}, {d}: ca hành chính cố định chưa hỗ trợ khi ADMIN khóa ở bước 2.6.')
            ctx.model.add(ctx.x[i,d,int(code)] == 1)
        elif code in NON_SHIFT_CODES:
            ctx.model.add(ctx.worked[i,d] == 0)
    return req


def add_credit_objective(ctx,data):
    """Dùng sau khi đã khóa ADMIN, thêm nhu cầu chính xác và Requests.
    Tối ưu theo thứ tự tuyệt đối: tổng vượt chuẩn, chênh max-min,
    rồi tổng độ lệch tuyệt đối quanh công trung bình (nhân n để dùng số nguyên).
    Không đặt điều kiện đủ công; công dưới chuẩn chỉ để báo cáo.
    """
    check_work_credit(data)
    demand = demand_requirements(data)
    req = request_map(data)
    people = [e for e in data.employees if e.group == 'SHIFT']
    if not people:
        raise ValueError('Không có nhân sự SHIFT.')
    m = ctx.model
    n, D = len(people), len(ctx.days)
    total, excess, missing = {}, {}, {}
    expected = sum(demand.values()) + sum(c in {'CT','DT'} for (i,d),c in req.items() if i in {e.id for e in people})
    deviations = []
    for e in people:
        i = e.id
        nonshift = sum(req.get((i,d)) in {'CT','DT'} for d in ctx.days)
        target = target_credit(data,e)
        total[i] = m.new_int_var(0,D,f'total_credit_{i}')
        m.add(total[i] == sum(ctx.worked[i,d] for d in ctx.days)+nonshift)
        excess[i] = m.new_int_var(0,D,f'excess_credit_{i}')
        missing[i] = m.new_int_var(0,target,f'missing_credit_{i}')
        m.add_max_equality(excess[i],[total[i]-target,0])
        m.add_max_equality(missing[i],[target-total[i],0])
        dev = m.new_int_var(0,max(n*D,expected),f'credit_deviation_{i}')
        m.add_abs_equality(dev,n*total[i]-expected)
        deviations.append(dev)
    # Vì tổng ca được cố định và ADMIN bị khóa, tổng công nhóm SHIFT là hằng số.
    m.add(sum(total.values()) == expected)
    hi = m.new_int_var(0,D,'max_credit')
    lo = m.new_int_var(0,D,'min_credit')
    m.add_max_equality(hi,list(total.values()))
    m.add_min_equality(lo,list(total.values()))
    spread = m.new_int_var(0,D,'credit_spread')
    m.add(spread == hi-lo)
    max_dev = n*max(n*D,expected)
    deviation = m.new_int_var(0,max_dev,'total_credit_deviation')
    m.add(deviation == sum(deviations))
    # Các hệ số được tính từ cận trên, không phải trọng số chọn tùy ý.
    w_spread = max_dev+1
    w_excess = D*w_spread+max_dev+1
    objective = w_excess*sum(excess.values()) + w_spread*spread + deviation
    m.minimize(objective)
    return CreditModel(total,excess,missing,spread,deviation,objective,expected)
