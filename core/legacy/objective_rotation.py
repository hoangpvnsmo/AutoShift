"""Bước 4.5: E04b cận chặt hơn và E05 chuyển ca."""
from dataclasses import dataclass
from math import isfinite
from core.optimization.objective_days_v2 import Quality431,solve_quality431
from core.optimization.objective_night import Layer,flag,score_workforce
from core.objectives.rest_checks import rest_rows,rest_scores,rest_windows
from core.objectives.rest_constraints import add_rest_balance
from core.optimization.objective_rest import refine_rest
from core.objectives.rest_bound_v2 import weekend_lower_v2
from core.optimization.objective_lex_v2 import refine_lex
from core.objectives.rotation_checks import rotation_rows,rotation_score
from core.objectives.rotation_constraints import add_rotation_balance
from core.objectives.quality_checks import shift_balance_rows,shift_balance_score
from core.objectives.night_checks import night_rows,night_score
from core.objectives.day_balance_checks import day_balance_rows
from core.objectives.day_balance_v2 import day_score_v2

@dataclass
class Quality45(Quality431):
    rest_a: Layer|None=None
    rest_b: Layer|None=None
    rotation: Layer|None=None
    rest_b_lower: tuple|None=None
    @property
    def proven(self):
        return super().proven and all(l is not None and l.status in ('OPTIMAL','PROVEN_BOUND','DISABLED','NO_DAYS') for l in (self.rest_a,self.rest_b,self.rotation))


def solve_quality45(ctx,data,credit,demand,base_time=120,quality_time=120,night_time=120,holiday_time=120,weekend_time=120,ideal_time=20,rest_time=60,rest_weekend_time=60,rotation_time=60):
    if any(not isfinite(t) or t<=0 for t in (rest_time,rest_weekend_time,rotation_time)): raise ValueError('Giới hạn E04/E05 phải hữu hạn, >0.')
    q=Quality45(**vars(solve_quality431(ctx,data,credit,demand,base_time,quality_time,night_time,holiday_time,weekend_time,ideal_time)))
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
    else:
        r=add_rest_balance(ctx,data)
        q.selected,q.rest_a=refine_rest(ctx,q.selected,lambda a:score(a,0),r.terms_a,r.obj_a,(0,0,0),rest_time,ideal_time,verify)
        q.rest_b_lower=weekend_lower_v2(data,ctx.days,w,demand,q.workforce[1],q.weekend)
        n=sum(e.group=='SHIFT' for e in data.employees); W=len(w.weekends); D=len(ctx.days)
        q.selected,q.rest_b=refine_lex(ctx,q.selected,lambda a:score(a,1),r.terms_b,r.obj_b,(n,n*W,W,D),q.rest_b_lower,rest_weekend_time,ideal_time,verify)
    def verify_rotation(a):
        verify(a)
        if q.rest_b.status!='DISABLED' and score(a,1)!=q.rest_b.after: raise RuntimeError('E05 thay đổi E04b.')
    def rotation(a): return rotation_score(rotation_rows(data,ctx.days,a))
    if not flag(data,'minimize_shift_changes'):
        v=rotation(q.selected.assignments); q.rotation=Layer('DISABLED',None,v,v,'Tắt minimize_shift_changes.')
    else:
        t=add_rotation_balance(ctx,data)
        q.selected,q.rotation=refine_lex(ctx,q.selected,rotation,t.terms,t.objective,t.bounds,(0,0,0,0),rotation_time,ideal_time,verify_rotation)
    verify_rotation(q.selected.assignments)
    if rotation(q.selected.assignments)!=q.rotation.after: raise RuntimeError('Sai điểm E05.')
    for layer,fn in old:
        if layer.status=='DISABLED': layer.after=fn(q.selected.assignments)
    for j,layer in enumerate((q.rest_a,q.rest_b)):
        if layer.status=='DISABLED': layer.after=score(q.selected.assignments,j)
    return q
