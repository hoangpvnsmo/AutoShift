"""E03: giảm max-min số ca của SHIFT, ngày lễ và cuối tuần tách riêng."""
from dataclasses import dataclass

@dataclass
class DayBalanceObjective:
    counts: dict
    gap: object


def add_day_balance(ctx,data,dates,kind):
    if kind not in ('holiday','weekend'): raise ValueError('Loại ngày không hợp lệ.')
    m=ctx.model; D=len(dates); counts={}
    for e in data.employees:
        if e.group!='SHIFT': continue
        v=m.new_int_var(0,D,f'E03_{kind}_count_{e.id}')
        m.add(v==sum(ctx.worked[e.id,d] for d in dates)); counts[e.id]=v
    gap=m.new_int_var(0,D,f'E03_{kind}_gap')
    if len(counts)<=1:
        m.add(gap==0)
    else:
        hi=m.new_int_var(0,D,f'E03_{kind}_max'); lo=m.new_int_var(0,D,f'E03_{kind}_min')
        m.add_max_equality(hi,list(counts.values())); m.add_min_equality(lo,list(counts.values()))
        m.add(gap==hi-lo)
    return DayBalanceObjective(counts,gap)
