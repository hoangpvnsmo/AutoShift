"""4.3.1: giữ engine E01/E02, nâng cấp hai tầng E03."""
from dataclasses import dataclass
from math import isfinite
from core.optimization.solver import solve
from core.optimization.objective import lock_workforce
from core.optimization.objective_night import Layer, flag, score_workforce
from core.optimization.objective_days import refine_bounded
from core.objectives.soft_constraints import add_shift_balance
from core.objectives.night_constraints import add_night_balance
from core.objectives.quality_checks import shift_balance_rows, shift_balance_score
from core.objectives.night_checks import night_rows, night_score, night_lower_bound
from core.objectives.day_balance_checks import day_sets, day_balance_rows, e01_lower_bound
from core.objectives.day_balance_v2 import day_score_v2, day_bound, add_day_balance_v2
from core.optimization.objective_day_v2 import refine_day

@dataclass
class Quality431:
    baseline: object
    selected: object
    workforce: tuple | None
    e01: Layer | None=None
    e02: Layer | None=None
    holiday: Layer | None=None
    weekend: Layer | None=None

    @property
    def proven(self):
        return self.baseline.status=='OPTIMAL' and all(
            x is not None and x.status in ('OPTIMAL','PROVEN_BOUND','DISABLED','NO_DAYS')
            for x in (self.e01,self.e02,self.holiday,self.weekend))


def solve_quality431(ctx,data,credit,demand,base_time=120,quality_time=120,night_time=120,holiday_time=120,weekend_time=120,ideal_time=20):
    if any(not isfinite(t) or t<=0 for t in (base_time,quality_time,night_time,holiday_time,weekend_time)):
        raise ValueError('Giới hạn thời gian phải hữu hạn và > 0.')
    if not isfinite(ideal_time) or ideal_time<0: raise ValueError('ideal_time phải hữu hạn và >=0.')
    enabled=[flag(data,k) for k in ('balance_shift_types','balance_night_shifts','balance_holiday_shifts','balance_weekend_shifts')]
    base=solve(ctx,time_limit=base_time)
    if base.assignments is None: return Quality431(base,base,None)
    selected=base; wf=score_workforce(ctx,data,base.assignments)
    lock_workforce(ctx,credit,wf)
    locked=[]
    def verify(a):
        if score_workforce(ctx,data,a)!=wf: raise RuntimeError('Thay đổi điểm stage 3.')
        for name,score,value in locked:
            if score(a)!=value: raise RuntimeError(f'Thay đổi điểm {name}.')
    def s1(a): return shift_balance_score(shift_balance_rows(data,ctx.days,a))
    def s2(a): return night_score(night_rows(data,ctx.days,a))
    def sh(a): return day_score_v2(day_balance_rows(data,ctx.days,a),'holiday')
    def sw(a): return day_score_v2(day_balance_rows(data,ctx.days,a),'weekend')
    scores=[s1,s2,sh,sw]; layers=[]; quality=None
    holidays,weekends=day_sets(data,ctx.days)
    for j,(name,active,score,limit) in enumerate(zip(('E01','E02','E03 lễ','E03 cuối tuần'),enabled,scores,(quality_time,night_time,holiday_time,weekend_time))):
        before=score(selected.assignments)
        if not active:
            layers.append(Layer('DISABLED',None,before,before,'Tắt trong cấu hình; không khóa điểm.')); continue
        if j>=2 and not (holidays if j==2 else weekends):
            layers.append(Layer('NO_DAYS',None,before,before,'Không có ngày thuộc nhóm; không cần giải.')); continue
        if j==0:
            quality=add_shift_balance(ctx,data)
            terms=(quality.worst_gap,quality.total_gap); obj=quality.objective
            lower=e01_lower_bound(data,ctx.days,demand,wf)
        elif j==1:
            night=add_night_balance(ctx,data,quality.counts if quality else None)
            terms=(night.worst_gap,night.total_gap); obj=night.objective
            lower=night_lower_bound(data,demand)
        else:
            dates=holidays if j==2 else weekends
            q,terms,obj=add_day_balance_v2(ctx,data,dates,'holiday' if j==2 else 'weekend')
            bound=day_bound(data,dates,demand,wf[1]); lower=bound.lower
        if j>=2:
            selected,layer=refine_day(ctx,selected,score,q,terms,obj,bound,limit,ideal_time,verify)
        else:
            selected,layer=refine_bounded(ctx,selected,score,terms,obj,lower,limit,verify)
        layers.append(layer); locked.append((name,score,layer.after))
    verify(selected.assignments)
    for active,score,layer in zip(enabled,scores,layers):
        if not active: layer.after=score(selected.assignments)
    return Quality431(base,selected,wf,*layers)
