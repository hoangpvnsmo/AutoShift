"""Adaptive LNS followed by unrestricted search. Only global proof is reusable."""
from time import perf_counter
from core.optimization.adaptive_policy import AdaptivePolicy
from core.objectives.flexible_rotation import flexible_targets
from core.optimization.lns_neighborhood import choose_neighborhood, make_rng, rotation_targets
from core.optimization.objective_lns import clone_neighborhood, refine_lns
from core.optimization.objective_days import solve_to_bound
from core.optimization.objective_lex_v2 import refine_lex, scalar
from core.optimization.objective_night import Layer
from core.optimization.solver import SolveResult
from core.objectives.preference_checks import preference_rows


def refine_alns(ctx,data,selected,score,terms,obj,bounds,lower,limit,ideal_time,verify,*,kind,prefs=(),fraction=.5,slice_time=5.,seed=0,tie_key=None,adaptive=True,stall_limit=5):
    before=score(selected.assignments)
    if before<lower: raise RuntimeError('Cận ALNS sai.')
    if not adaptive or fraction<=0 or before==lower:
        return refine_lns(ctx,data,selected,score,terms,obj,bounds,lower,limit,ideal_time,verify,kind=kind,prefs=prefs,fraction=fraction,slice_time=slice_time,seed=seed,tie_key=tie_key)
    start=perf_counter(); policy=AdaptivePolicy(limit,fraction,slice_time,stall_limit)
    rng=make_rng(selected.assignments,seed); best=selected; point=before
    counts={}; details=[]; stop=''
    while point>lower and policy.attempts<40 and policy.remaining_local(perf_counter()-start)>.05:
        targets=(rotation_targets(data,ctx.days,best.assignments) if kind=='rotation' else flexible_targets(data,ctx.days,best.assignments) if kind=='flexible' else
                 [(p.employee_id,p.day,p.weight) for p,c,ok,impossible in preference_rows(data,ctx.days,best.assignments,prefs) if not ok and not impossible])
        mode=policy.select(rng,bool(targets)); t0=perf_counter()
        nb=choose_neighborhood(ctx.employee_ids,ctx.days,targets,policy.iteration(mode),rng)
        if not nb.cells or len(nb.cells)==len(ctx.worked):
            stop='vùng đã phủ toàn mô hình hoặc rỗng'; break
        trial=clone_neighborhood(ctx,best.assignments,nb.cells)
        trial_terms=tuple(trial.model.get_int_var_from_proto_index(v.index) for v in terms)
        expr=0
        for v,b in zip(trial_terms,bounds): expr=expr*(b+1)+v
        trial.model.add(expr>=scalar(lower,bounds)); trial.model.add(expr<=scalar(point,bounds)-1); trial.model.minimize(expr)
        remain=policy.remaining_local(perf_counter()-start)
        if remain<=.05: break
        r=solve_to_bound(trial,trial_terms,lower,min(slice_time,remain))
        counts[r.status]=counts.get(r.status,0)+1
        if r.status=='MODEL_INVALID': raise RuntimeError('ALNS MODEL_INVALID; không lưu nghiệm.')
        previous=point
        if r.assignments is not None:
            verify(r.assignments); new=score(r.assignments)
            if new<lower or new>=point: raise RuntimeError('Điểm ALNS không khớp ràng buộc cải thiện.')
            if any(r.assignments[k]!=best.assignments[k] for k in best.assignments if k not in nb.cells): raise RuntimeError('ALNS thay đổi ô cố định.')
            best=r; point=new; details.append(f'{nb.kind}:{point}')
        policy.record(mode,previous,point,perf_counter()-t0)
    elapsed=perf_counter()-start
    reason='đạt cận' if point==lower else stop or ('giới hạn 40 vùng' if policy.attempts>=40 else policy.reason(elapsed))
    note=(f'ALNS {kind}: {policy.attempts} lượt, {policy.improvements} cải thiện, {elapsed:.2f}s; '
          f'quota {policy.initial:.0%}->{policy.cap:.0%}; {before}->{point}; dừng: {reason}; '
          f'operator [{policy.summary()}]; trạng thái vùng {counts}. ')
    if details: note+='Các nghiệm tốt hơn: '+', '.join(details)+'. '
    remain=limit-(perf_counter()-start)
    if point==lower or remain<=.05:
        for v,b in zip(terms,point): ctx.model.add(v==b)
        status='PROVEN_BOUND' if point==lower else 'FEASIBLE'
        return best,Layer(status,SolveResult(status,best.assignments,perf_counter()-start),before,point,note)
    final,layer=refine_lex(ctx,best,score,terms,obj,bounds,lower,remain,0,verify)
    if layer.after==point and (tie_key is None or tie_key(best.assignments)<=tie_key(final.assignments)): final=best
    if layer.status=='UNKNOWN': layer.status='FEASIBLE'
    layer.before=before; layer.result=SolveResult(layer.status,final.assignments,perf_counter()-start)
    layer.note=note+f'Toàn cục (ngân sách {remain:.2f}s): '+layer.note
    return final,layer
