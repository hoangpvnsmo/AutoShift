"""Refine với cận từ điển, clone thử nghiệm, incumbent và ngân sách chung."""
from dataclasses import replace
from time import perf_counter
from core.optimization.objective_night import Layer,hint
from core.optimization.objective_days import solve_to_bound
from core.optimization.solver import solve,SolveResult


def scalar(values,bounds):
    z=0
    for v,b in zip(values,bounds):
        if not 0<=v<=b: raise ValueError('Điểm ngoài miền.')
        z=z*(b+1)+v
    return z


def refine_lex(ctx,selected,score,terms,obj,bounds,lower,limit,ideal_time,verify):
    before=score(selected.assignments); incumbent=before
    if before<lower: raise RuntimeError(f'Cận từ điển sai: {lower}, nền {before}.')
    m=ctx.model; low=scalar(lower,bounds); top=scalar(before,bounds)
    m.add(obj>=low); m.add(obj<=top)
    def finish(status,result,note):
        for v,b in zip(terms,incumbent): m.add(v==b)
        return selected,Layer(status,result,before,incumbent,note)
    if before==lower: return finish('PROVEN_BOUND',None,f'Đã đạt cận {lower}; bỏ qua giải.')
    start=perf_counter(); elapsed=0.; note=''; budget=min(ideal_time,limit/2)
    if budget>0:
        trial_m=m.clone(); var=lambda v:trial_m.get_int_var_from_proto_index(v.index)
        trial=replace(ctx,model=trial_m,x={k:var(v) for k,v in ctx.x.items()},worked={k:var(v) for k,v in ctx.worked.items()})
        trial_m.clear_objective()
        for v,b in zip(terms,lower): trial_m.add(var(v)==b)
        hint(trial,selected.assignments)
        r=solve(trial,budget); elapsed=r.wall_time
        note=f'Thử cận {lower}: {r.status} ({elapsed:.2f}s). '
        if r.status=='MODEL_INVALID': raise RuntimeError('Mô hình thử không hợp lệ.')
        if r.assignments is not None:
            verify(r.assignments); incumbent=score(r.assignments)
            if incumbent!=lower: raise RuntimeError('Nghiệm thử sai cận.')
            selected=r
            return finish('PROVEN_BOUND',r,note+'Đạt cận; khóa điểm.')
        if r.status=='INFEASIBLE':
            # Chỉ loại điểm cận khi chứng minh vô nghiệm; không loại nếu UNKNOWN.
            m.add(obj>=low+1)
            if top==low+1:
                return finish('PROVEN_BOUND',SolveResult(r.status,None,elapsed),note+'Cận không khả thi; incumbent là điểm nguyên kế tiếp.')
    remain=limit-(perf_counter()-start)
    if remain<=0: return finish('UNKNOWN',SolveResult('UNKNOWN',None,elapsed),note+'Hết ngân sách; giữ lịch trước.')
    m.minimize(obj); hint(ctx,selected.assignments)
    r=solve_to_bound(ctx,terms,lower,remain)
    logged=SolveResult(r.status,r.assignments,r.wall_time+elapsed)
    if r.status in ('INFEASIBLE','MODEL_INVALID'): raise RuntimeError(f'{r.status} dù có lịch nền.')
    if r.assignments is None: return finish(r.status,logged,note+'Chưa có nghiệm mới; giữ lịch trước.')
    verify(r.assignments); after=score(r.assignments)
    if after<lower or after>before: raise RuntimeError('Nghiệm mới ngoài cận/incumbent.')
    incumbent=after; selected=r
    return finish('PROVEN_BOUND' if after==lower else r.status,logged,note+'Chấp nhận nghiệm không kém hơn lịch trước.')
