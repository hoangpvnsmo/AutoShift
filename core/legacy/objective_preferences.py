"""E06 sau E05: dùng nguyên mô hình và các điểm đã khóa của 4.5."""
from dataclasses import dataclass
from math import isfinite
from core.legacy.objective_rotation import Quality45,solve_quality45
from core.optimization.objective_night import Layer,flag,score_workforce
from core.optimization.objective_lex_v2 import refine_lex
from core.objectives.preference_checks import validate_preferences,preference_rows,preference_score,preference_lower
from core.objectives.preference_constraints import add_preferences
from core.objectives.quality_checks import shift_balance_rows,shift_balance_score
from core.objectives.night_checks import night_rows,night_score
from core.objectives.day_balance_checks import day_balance_rows
from core.objectives.day_balance_v2 import day_score_v2
from core.objectives.rest_checks import rest_rows,rest_scores,rest_windows
from core.objectives.rotation_checks import rotation_rows,rotation_score

@dataclass
class Quality46(Quality45):
    preferences: Layer|None=None
    preference_lower: tuple|None=None
    @property
    def proven(self):
        return super().proven and self.preferences is not None and self.preferences.status in ('OPTIMAL','PROVEN_BOUND','DISABLED','NO_PREFERENCES')


def solve_quality46(ctx,data,credit,demand,base_time=120,quality_time=120,night_time=120,holiday_time=120,weekend_time=120,ideal_time=20,rest_time=60,rest_weekend_time=60,rotation_time=60,preferences=(),preference_time=30):
    if not isfinite(preference_time) or preference_time<=0: raise ValueError('preference_time phải hữu hạn và >0.')
    preferences=validate_preferences(data,tuple(preferences)); active=flag(data,'honor_preferences')
    q=Quality46(**vars(solve_quality45(ctx,data,credit,demand,base_time,quality_time,night_time,holiday_time,weekend_time,ideal_time,rest_time,rest_weekend_time,rotation_time)))
    if q.selected.assignments is None: return q
    w=rest_windows(data,ctx.days)
    def rest(a,j): return rest_scores(rest_rows(data,ctx.days,a,w),len(w.weekends))[j]
    prior=[(q.e01,lambda a:shift_balance_score(shift_balance_rows(data,ctx.days,a))),
           (q.e02,lambda a:night_score(night_rows(data,ctx.days,a))),
           (q.holiday,lambda a:day_score_v2(day_balance_rows(data,ctx.days,a),'holiday')),
           (q.weekend,lambda a:day_score_v2(day_balance_rows(data,ctx.days,a),'weekend')),
           (q.rest_a,lambda a:rest(a,0)),(q.rest_b,lambda a:rest(a,1)),
           (q.rotation,lambda a:rotation_score(rotation_rows(data,ctx.days,a)))]
    def verify(a):
        if score_workforce(ctx,data,a)!=q.workforce: raise RuntimeError('E06 thay đổi stage 3.')
        for layer,fn in prior:
            if layer.status!='DISABLED' and fn(a)!=layer.after: raise RuntimeError('E06 thay đổi điểm ưu tiên đã khóa.')
    def score(a): return preference_score(preference_rows(data,ctx.days,a,preferences))
    before=score(q.selected.assignments); q.preference_lower=preference_lower(data,preferences)
    if not active: q.preferences=Layer('DISABLED',None,before,before,'Tắt honor_preferences.')
    elif not preferences: q.preferences=Layer('NO_PREFERENCES',None,before,before,'Không có nguyện vọng mềm; bỏ qua giải.')
    else:
        terms,obj,bounds=add_preferences(ctx,data,preferences)
        q.selected,q.preferences=refine_lex(ctx,q.selected,score,terms,obj,bounds,q.preference_lower,preference_time,ideal_time,verify)
    verify(q.selected.assignments)
    if score(q.selected.assignments)!=q.preferences.after: raise RuntimeError('Sai điểm E06.')
    for layer,fn in prior:
        if layer.status=='DISABLED': layer.after=fn(q.selected.assignments)
    return q
