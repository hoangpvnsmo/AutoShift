"""Bước 4.3: tối ưu phân cấp + cận dưới + callback dừng sớm.
Không thay đổi solver/runner cũ. Mỗi tầng giữ điểm đạt được, không khóa lịch.
"""
from dataclasses import dataclass
from math import isfinite
from ortools.sat.python import cp_model
from core.optimization.solver import solve, SolveResult
from core.optimization.objective import lock_workforce
from core.optimization.objective_night import Layer, flag, score_workforce, hint
from core.objectives.soft_constraints import add_shift_balance
from core.objectives.night_constraints import add_night_balance
from core.objectives.quality_checks import shift_balance_rows, shift_balance_score
from core.objectives.night_checks import night_rows, night_score, night_lower_bound
from core.objectives.day_balance_checks import day_sets, day_balance_rows, day_score, day_lower_bound, e01_lower_bound
from core.objectives.day_balance_constraints import add_day_balance


class StopAtBound(cp_model.CpSolverSolutionCallback):
    def __init__(self, terms, lower):
        super().__init__(); self.terms=terms; self.lower=lower; self.reached=False
    def on_solution_callback(self):
        if tuple(self.value(v) for v in self.terms)==self.lower:
            self.reached=True; self.stop_search()


def solve_to_bound(ctx, terms, lower, limit):
    solver=cp_model.CpSolver(); solver.parameters.max_time_in_seconds=limit
    cb=StopAtBound(terms,lower)
    status=solver.solve(ctx.model,cb)
    a=None
    if status in (cp_model.OPTIMAL,cp_model.FEASIBLE):
        a={(i,d):next((s for s in ctx.shifts if solver.value(ctx.x[i,d,s])==1),0)
           for i in ctx.employee_ids for d in ctx.days}
    return SolveResult(solver.status_name(status),a,solver.wall_time)


def refine_bounded(ctx,selected,score,terms,objective,lower,limit,verify):
    """Cận dưới từng thành phần phải được chứng minh trước khi gọi."""
    before=score(selected.assignments)
    if any(v<b for v,b in zip(before,lower)):
        raise RuntimeError(f'Cận dưới không hợp lệ: {lower}, lịch nền {before}.')
    # Các cận dưới riêng lẻ là ràng buộc suy ra hợp lệ; hỗ trợ presolve.
    for term,b in zip(terms,lower): ctx.model.add(term>=b)
    if before==lower:
        layer=Layer('PROVEN_BOUND',None,before,before,f'Đã đạt cận dưới {lower}; bỏ qua lượt giải.')
    else:
        ctx.model.minimize(objective)
        hint(ctx,selected.assignments)
        result=solve_to_bound(ctx,terms,lower,limit)
        if result.status in ('INFEASIBLE','MODEL_INVALID'):
            raise RuntimeError(f'{result.status} dù có lịch nền; kiểm tra mô hình/cận.')
        if result.assignments is None:
            layer=Layer(result.status,result,before,before,'Chưa có nghiệm mới; giữ lịch trước.')
        else:
            verify(result.assignments)
            after=score(result.assignments)
            if any(v<b for v,b in zip(after,lower)): raise RuntimeError('Điểm thực tế thấp hơn cận dưới.')
            if after>before:
                if result.status=='OPTIMAL': raise RuntimeError('Nghiệm tối ưu kém hơn lịch nền.')
                layer=Layer(result.status,result,before,before,'Nghiệm mới kém hơn; giữ lịch trước.')
            else:
                selected=result
                status='PROVEN_BOUND' if after==lower else result.status
                layer=Layer(status,result,before,after,
                            f'Đạt cận dưới {lower} (CP-SAT: {result.status}).' if after==lower else 'Chấp nhận nghiệm không kém hơn lịch trước.')
    # Quan trọng: khóa cả khi bỏ qua giải vì đã đạt cận dưới.
    for term,value in zip(terms,layer.after): ctx.model.add(term==value)
    return selected,layer


@dataclass
class Quality43:
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


def solve_quality43(ctx,data,credit,demand,base_time=120,quality_time=120,night_time=120,holiday_time=120,weekend_time=120):
    if any(not isfinite(t) or t<=0 for t in (base_time,quality_time,night_time,holiday_time,weekend_time)):
        raise ValueError('Giới hạn thời gian phải hữu hạn và > 0.')
    enabled=[flag(data,k) for k in ('balance_shift_types','balance_night_shifts','balance_holiday_shifts','balance_weekend_shifts')]
    base=solve(ctx,time_limit=base_time)
    if base.assignments is None: return Quality43(base,base,None)
    selected=base; wf=score_workforce(ctx,data,base.assignments)
    lock_workforce(ctx,credit,wf)
    locked=[]
    def verify(a):
        if score_workforce(ctx,data,a)!=wf: raise RuntimeError('Thay đổi điểm stage 3.')
        for name,score,value in locked:
            if score(a)!=value: raise RuntimeError(f'Thay đổi điểm {name}.')
    def s1(a): return shift_balance_score(shift_balance_rows(data,ctx.days,a))
    def s2(a): return night_score(night_rows(data,ctx.days,a))
    def sh(a): return day_score(day_balance_rows(data,ctx.days,a),'holiday')
    def sw(a): return day_score(day_balance_rows(data,ctx.days,a),'weekend')
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
            q=add_day_balance(ctx,data,dates,'holiday' if j==2 else 'weekend')
            terms=(q.gap,); obj=q.gap; lower=day_lower_bound(data,dates,demand,wf[1])
        selected,layer=refine_bounded(ctx,selected,score,terms,obj,lower,limit,verify)
        layers.append(layer); locked.append((name,score,layer.after))
    verify(selected.assignments)
    for active,score,layer in zip(enabled,scores,layers):
        if not active: layer.after=score(selected.assignments)
    return Quality43(base,selected,wf,*layers)
