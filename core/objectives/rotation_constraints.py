"""E05: biến chỉ báo chính xác cho các cặp chuyển ca khác loại."""
from dataclasses import dataclass
from datetime import timedelta
from core.validation.transition_checks import transition_config,gap_minutes
from core.objectives.rotation_checks import preferred_rest_minutes
from core.objectives.rest_constraints import lex_objective

@dataclass
class RotationModel:
    terms: tuple
    objective: object
    bounds: tuple


def add_rotation_balance(ctx,data):
    m=ctx.model; cfg=transition_config(data); threshold=preferred_rest_minutes(data)
    ids=[e.id for e in data.employees if e.group=='SHIFT']; D=len(ctx.days); n=len(ids)
    def total(items,cap,name):
        v=m.new_int_var(0,cap,name); m.add(v==sum(items)); return v
    counts=[]; shorts=[]
    for i in ids:
        changes=[]; short=[]
        history=data.previous[i,ctx.days[0]-timedelta(days=1)]
        for j,d in enumerate(ctx.days):
            for s in ctx.shifts:
                for t in ctx.shifts:
                    if s==t: continue
                    right=ctx.x[i,d,t]
                    if j==0:
                        if history!=str(s): continue
                        z=right
                    else:
                        left=ctx.x[i,ctx.days[j-1],s]
                        z=m.new_bool_var(f'e05_{i}_{d}_{s}_{t}')
                        m.add(z<=left); m.add(z<=right); m.add(z>=left+right-1)
                    changes.append(z)
                    if gap_minutes(cfg,s,t)<threshold: short.append(z)
        counts.append(total(changes,D,f'e05_changes_{i}'))
        shorts.append(total(short,D,f'e05_short_{i}'))
    worst=m.new_int_var(0,D,'e05_max'); lo=m.new_int_var(0,D,'e05_min')
    if counts:
        m.add_max_equality(worst,counts); m.add_min_equality(lo,counts)
    else: m.add(worst==0); m.add(lo==0)
    gap=m.new_int_var(0,D,'e05_gap'); m.add(gap==worst-lo)
    terms=(total(counts,n*D,'e05_total'),total(shorts,n*D,'e05_short_total'),worst,gap)
    bounds=(n*D,n*D,D,D)
    return RotationModel(terms,lex_objective(terms,bounds),bounds)
