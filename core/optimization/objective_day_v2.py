"""E03: thử nghiệm đạt cận trên bản sao; fallback không khóa nhầm ràng buộc thử."""
from dataclasses import replace
from time import perf_counter
from core.optimization.solver import solve, SolveResult
from core.optimization.objective_night import Layer
from core.optimization.objective_days import refine_bounded


def ideal_trial(ctx, q, terms, bound, limit):
    # clone giữ nguyên index biến; ánh xạ biến vào model bản sao.
    m=ctx.model.clone()
    def var(v): return m.get_int_var_from_proto_index(v.index)
    trial=replace(ctx,model=m,x={k:var(v) for k,v in ctx.x.items()},
                  worked={k:var(v) for k,v in ctx.worked.items()})
    m.clear_objective(); m.clear_hints()
    for v,b in zip(terms,bound.lower): m.add(var(v)==b)
    values=bound.ideal_values
    if values is not None and q.counts:
        # Phân bổ lý tưởng cụ thể. Khi A07 buộc có người 0 ca, dùng số
        # người khác 0 tối đa và các mức q/q+1; đây chỉ là thử đủ, không cần thiết.
        allowed=sorted(set(values)); count_vars=[var(v) for v in q.counts.values()]
        if 0 not in allowed or len(allowed)<=2 and allowed==list(range(allowed[0],allowed[-1]+1)):
            for v in count_vars: m.add(v>=allowed[0]); m.add(v<=allowed[-1])
        else:
            for v in count_vars: m.add_allowed_assignments([v],[(a,) for a in allowed])
        if 0 in allowed and any(values):
            used=[]
            for j,v in enumerate(count_vars):
                b=m.new_bool_var(f'ideal_used_{j}'); m.add(v>=1).only_enforce_if(b); m.add(v==0).only_enforce_if(~b); used.append(b)
            m.add(sum(used)==sum(x>0 for x in values))
    return solve(trial,time_limit=limit)


def refine_day(ctx, selected, score, q, terms, objective, bound, limit, trial_limit, verify):
    before=score(selected.assignments); lower=bound.lower
    if any(a<b for a,b in zip(before,lower)): raise RuntimeError(f'Cận E03 sai: {lower}, nền {before}')
    if before==lower:
        return refine_bounded(ctx,selected,score,terms,objective,lower,limit,verify)
    start=perf_counter(); note=''; elapsed=0.0
    # Dành tối đa một nửa ngân sách tầng cho thử nghiệm, giữ phần còn lại fallback.
    budget=min(trial_limit,limit/2)
    if budget>0:
        trial=ideal_trial(ctx,q,terms,bound,budget); elapsed=trial.wall_time
        note=f'Thử cận {lower}: {trial.status} ({elapsed:.2f} s). '
        if trial.status=='MODEL_INVALID': raise RuntimeError('Mô hình thử E03 không hợp lệ.')
        if trial.assignments is not None:
            verify(trial.assignments); after=score(trial.assignments)
            if after!=lower: raise RuntimeError('Nghiệm thử không đạt cận đã khai báo.')
            for v,b in zip(terms,after): ctx.model.add(v==b)
            return trial,Layer('PROVEN_BOUND',trial,before,after,note+'Đạt cận; khóa điểm, không khóa lịch.')
        # UNKNOWN khác INFEASIBLE. Mô hình thử có thể chặt hơn điều kiện đạt cận;
        # kể cả INFEASIBLE cũng không kết luận không tồn tại nghiệm tối ưu chung.
    remain=limit-(perf_counter()-start)
    if remain<=0:
        for v,b in zip(terms,before): ctx.model.add(v==b)
        result=SolveResult('UNKNOWN',None,elapsed)
        return selected,Layer('UNKNOWN',result,before,before,note+'Hết ngân sách; giữ lịch nền.')
    # Không thêm ràng buộc thử vào mô hình chính. Giới hạn điểm không kém lịch nền.
    # Cắt nghiệm kém hơn theo thứ tự từ điển, không cắt nghiệm giảm gap.
    gap,dev=terms; m=ctx.model
    m.add(gap<=before[0])
    same=m.new_bool_var(f"incumbent_gap_{gap.index}")
    m.add(gap==before[0]).only_enforce_if(same)
    m.add(gap<before[0]).only_enforce_if(~same)
    m.add(dev<=before[1]).only_enforce_if(same)
    # refine_bounded giữ incumbent, truyền hint và khóa điểm cuối.
    selected,layer=refine_bounded(ctx,selected,score,terms,objective,lower,remain,verify)
    actual=layer.result.wall_time if layer.result is not None else 0.0
    layer.result=SolveResult(layer.result.status if layer.result else layer.status,
                             layer.result.assignments if layer.result else None,elapsed+actual)
    layer.note=note+'Fallback mềm: '+layer.note
    return selected,layer
