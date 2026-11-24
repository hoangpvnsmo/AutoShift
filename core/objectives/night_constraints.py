"""E02: biểu thức CP-SAT, không thêm ràng buộc cứng về mức chênh lệch."""
from dataclasses import dataclass
from core.objectives.night_checks import night_groups

@dataclass
class NightBalanceObjective:
    counts: dict
    gaps: dict
    worst_gap: object
    total_gap: object
    objective: object


def add_night_balance(ctx,data,counts=None):
    m=ctx.model; D=len(ctx.days); groups=night_groups(data)
    night={}; gaps={}
    for k,ids in groups.items():
        for i in ids:
            if counts is not None:
                night[i]=counts[i,1]  # tái sử dụng biến E01
            else:
                night[i]=m.new_int_var(0,D,f'E02_night_{i}')
                m.add(night[i]==sum(ctx.x[i,d,1] for d in ctx.days))
        j=len(gaps)
        gap=m.new_int_var(0,D,f'E02_gap_{j}'); gaps[k]=gap
        if len(ids)<=1:
            m.add(gap==0)
        else:
            hi=m.new_int_var(0,D,f'E02_max_{j}'); lo=m.new_int_var(0,D,f'E02_min_{j}')
            m.add_max_equality(hi,[night[i] for i in ids]); m.add_min_equality(lo,[night[i] for i in ids])
            m.add(gap==hi-lo)
    bound=len(gaps)*D
    worst=m.new_int_var(0,D,'E02_worst_gap'); total=m.new_int_var(0,bound,'E02_total_gap')
    if gaps: m.add_max_equality(worst,list(gaps.values()))
    else: m.add(worst==0)
    m.add(total==sum(gaps.values()))
    return NightBalanceObjective(night,gaps,worst,total,(bound+1)*worst+total)
