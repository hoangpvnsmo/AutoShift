"""Bước 4.1: các biến đo cân bằng loại ca của SHIFT."""
from dataclasses import dataclass

@dataclass
class ShiftBalanceObjective:
    counts: dict
    gaps: dict
    worst_gap: object
    total_gap: object
    objective: object


def add_shift_balance(ctx, data):
    m = ctx.model
    D = len(ctx.days)
    counts, gaps = {}, {}
    for e in data.employees:
        if e.group != 'SHIFT':
            continue
        i = e.id
        for s in ctx.shifts:
            counts[i,s] = m.new_int_var(0,D,f'quality_count_{i}_{s}')
            m.add(counts[i,s] == sum(ctx.x[i,d,s] for d in ctx.days))
        allowed = sorted(set(e.allowed_shifts))
        gap = m.new_int_var(0,D,f'quality_gap_{i}')
        gaps[i] = gap
        if len(allowed) <= 1:
            m.add(gap == 0)
        else:
            hi = m.new_int_var(0,D,f'quality_max_{i}')
            lo = m.new_int_var(0,D,f'quality_min_{i}')
            m.add_max_equality(hi,[counts[i,s] for s in allowed])
            m.add_min_equality(lo,[counts[i,s] for s in allowed])
            m.add(gap == hi-lo)
    if not gaps:
        raise ValueError('Cần ít nhất một nhân sự SHIFT.')
    bound = len(gaps)*D
    worst = m.new_int_var(0,D,'quality_worst_gap')
    total = m.new_int_var(0,bound,'quality_total_gap')
    m.add_max_equality(worst,list(gaps.values()))
    m.add(total == sum(gaps.values()))
    # Cận trên total_gap = n*D; giảm worst 1 luôn tốt hơn mọi thay đổi total.
    objective = (bound+1)*worst + total
    return ShiftBalanceObjective(counts,gaps,worst,total,objective)
