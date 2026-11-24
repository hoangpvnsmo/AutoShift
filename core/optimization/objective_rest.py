"""Tái dùng engine 4.3.1, thêm hai tầng E04. Không sửa code nền."""
from dataclasses import dataclass,replace
from math import isfinite
from time import perf_counter
from core.optimization.objective_days_v2 import Quality431,solve_quality431
from core.optimization.objective_night import Layer,flag,score_workforce,hint
from core.optimization.objective_days import refine_bounded
from core.optimization.solver import solve,SolveResult
from core.objectives.rest_checks import rest_rows,rest_scores,rest_windows,weekend_lower
from core.objectives.rest_constraints import add_rest_balance
from core.objectives.quality_checks import shift_balance_rows,shift_balance_score
from core.objectives.night_checks import night_rows,night_score
from core.objectives.day_balance_checks import day_balance_rows
from core.objectives.day_balance_v2 import day_score_v2

@dataclass
class Quality44(Quality431):
    rest_a: Layer|None=None
    rest_b: Layer|None=None
    @property
    def proven(self):
        return super().proven and all(l is not None and l.status in ('OPTIMAL','PROVEN_BOUND','DISABLED','NO_DAYS') for l in (self.rest_a,self.rest_b))


def refine_rest(ctx,selected,score,terms,obj,lower,limit,ideal_time,verify):
    """Thử cận trên clone (không khóa nhầm), rồi fallback trong ngân sách tầng."""
    before=score(selected.assignments)
    if any(a<b for a,b in zip(before,lower)): raise RuntimeError('Cận E04 sai.')
    if before==lower: return refine_bounded(ctx,selected,score,terms,obj,lower,limit,verify)
    start=perf_counter(); elapsed=0.; note=''; budget=min(ideal_time,limit/2)
    if budget>0:
        m=ctx.model.clone(); var=lambda v:m.get_int_var_from_proto_index(v.index)
        trial=replace(ctx,model=m,x={k:var(v) for k,v in ctx.x.items()},worked={k:var(v) for k,v in ctx.worked.items()})
        m.clear_objective()
        for v,b in zip(terms,lower): m.add(var(v)==b)
        hint(trial,selected.assignments)
        result=solve(trial,time_limit=budget); elapsed=result.wall_time
        note=f'Thử cận {lower}: {result.status} ({elapsed:.2f}s). '
        if result.status=='MODEL_INVALID': raise RuntimeError('Mô hình thử E04 không hợp lệ.')
        if result.assignments is not None:
            verify(result.assignments); after=score(result.assignments)
            if after!=lower: raise RuntimeError('Nghiệm thử E04 không đạt cận.')
            for v,b in zip(terms,after): ctx.model.add(v==b)
            return result,Layer('PROVEN_BOUND',result,before,after,note+'Đạt cận; khóa điểm.')
    remain=limit-(perf_counter()-start)
    if remain<=0:
        for v,b in zip(terms,before): ctx.model.add(v==b)
        return selected,Layer('UNKNOWN',SolveResult('UNKNOWN',None,elapsed),before,before,note+'Hết ngân sách; giữ lịch trước.')
    # Objective scalar đã mã hóa thứ tự từ điển; cận incumbent không loại lịch nền.
    # refine_bounded kiểm tra điểm độc lập và giữ nghiệm nền nếu không cải thiện.
    selected,layer=refine_bounded(ctx,selected,score,terms,obj,lower,remain,verify)
    if layer.result is not None:
        layer.result=SolveResult(layer.result.status,layer.result.assignments,layer.result.wall_time+elapsed)
    layer.note=note+layer.note
    return selected,layer


def solve_quality44(ctx,data,credit,demand,base_time=120,quality_time=120,night_time=120,holiday_time=120,weekend_time=120,ideal_time=20,rest_time=60,rest_weekend_time=60):
    if any(not isfinite(t) or t<=0 for t in (rest_time,rest_weekend_time)): raise ValueError('Giới hạn E04 phải hữu hạn, >0.')
    q=Quality44(**vars(solve_quality431(ctx,data,credit,demand,base_time,quality_time,night_time,holiday_time,weekend_time,ideal_time)))
    if q.selected.assignments is None: return q
    w=rest_windows(data,ctx.days)
    def s1(a): return shift_balance_score(shift_balance_rows(data,ctx.days,a))
    def s2(a): return night_score(night_rows(data,ctx.days,a))
    def sh(a): return day_score_v2(day_balance_rows(data,ctx.days,a),'holiday')
    def sw(a): return day_score_v2(day_balance_rows(data,ctx.days,a),'weekend')
    old=[(q.e01,s1),(q.e02,s2),(q.holiday,sh),(q.weekend,sw)]
    def score(a,j): return rest_scores(rest_rows(data,ctx.days,a,w),len(w.weekends))[j]
    def verify(a):
        if score_workforce(ctx,data,a)!=q.workforce: raise RuntimeError('E04 thay đổi điểm stage 3.')
        for layer,fn in old:
            if layer.status!='DISABLED' and fn(a)!=layer.after: raise RuntimeError('E04 thay đổi điểm đã khóa E01/E02/E03.')
        if q.rest_a is not None and q.rest_a.status!='DISABLED' and score(a,0)!=q.rest_a.after: raise RuntimeError('Thay đổi điểm E04a.')
    if not flag(data,'balance_rest_days'):
        for j,key in enumerate(('rest_a','rest_b')):
            v=score(q.selected.assignments,j); setattr(q,key,Layer('DISABLED',None,v,v,'Tắt balance_rest_days.'))
        return q
    r=add_rest_balance(ctx,data)
    q.selected,q.rest_a=refine_rest(ctx,q.selected,lambda a:score(a,0),r.terms_a,r.obj_a,(0,0,0),rest_time,ideal_time,verify)
    q.selected,q.rest_b=refine_rest(ctx,q.selected,lambda a:score(a,1),r.terms_b,r.obj_b,weekend_lower(data,w,demand,q.workforce[1]),rest_weekend_time,ideal_time,verify)
    verify(q.selected.assignments)
    if score(q.selected.assignments,1)!=q.rest_b.after: raise RuntimeError('Sai điểm E04b cuối cùng.')
    for layer,fn in old:
        if layer.status=='DISABLED': layer.after=fn(q.selected.assignments)
    return q
