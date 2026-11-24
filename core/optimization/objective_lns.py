"""Search-only enhancement. Restricted solves never prove global optimality."""
from dataclasses import replace
from time import perf_counter
from core.objectives.flexible_rotation import flexible_targets
from core.optimization.lns_neighborhood import choose_neighborhood, make_rng, rotation_targets
from core.optimization.objective_night import hint, Layer
from core.optimization.objective_days import solve_to_bound
from core.optimization.objective_lex_v2 import refine_lex, scalar
from core.optimization.solver import SolveResult
from core.objectives.preference_checks import preference_rows


def clone_neighborhood(ctx, assignments, cells):
    """Clone all hard constraints + prior locks. Freeze only x outside cells."""
    m=ctx.model.clone(); m.clear_objective(); m.clear_hints()
    var=lambda v:m.get_int_var_from_proto_index(v.index)
    trial=replace(ctx,model=m,x={k:var(v) for k,v in ctx.x.items()},worked={k:var(v) for k,v in ctx.worked.items()})
    for (i,d,s),v in trial.x.items():
        if (i,d) not in cells: m.add(v==int(assignments[i,d]==s))
    hint(trial,assignments)
    return trial


def refine_lns(ctx,data,selected,score,terms,obj,bounds,lower,limit,ideal_time,verify,*,kind,prefs=(),fraction=.5,slice_time=5.,seed=0,tie_key=None):
    """Spend fraction of total wall budget on strictly improving neighborhoods.
    Unrestricted refinement locks the final point on the original model.
    """
    before=score(selected.assignments)
    if before<=lower or fraction<=0:
        return refine_lex(ctx,selected,score,terms,obj,bounds,lower,limit,ideal_time,verify)
    start=perf_counter(); end=start+limit*fraction
    rng=make_rng(selected.assignments,seed); attempts=improved=0; counts={}; details=[]
    best=selected; best_score=before
    while best_score>lower and attempts<12 and end-perf_counter()>.05:
        if kind=='rotation':
            targets=rotation_targets(data,ctx.days,best.assignments)
        elif kind=='flexible':
            targets=flexible_targets(data,ctx.days,best.assignments)
        else:
            targets=[(p.employee_id,p.day,p.weight) for p,c,ok,impossible in preference_rows(data,ctx.days,best.assignments,prefs) if not ok and not impossible]
        nb=choose_neighborhood(ctx.employee_ids,ctx.days,targets,attempts,rng)
        if not nb.cells or len(nb.cells)==len(ctx.worked): break
        trial=clone_neighborhood(ctx,best.assignments,nb.cells)
        v=lambda term:trial.model.get_int_var_from_proto_index(term.index)
        trial_terms=tuple(v(term) for term in terms)
        # Reconstruct the linear objective on clone variables (no cross-model object).
        expr=0
        for term,b in zip(trial_terms,bounds): expr=expr*(b+1)+term
        trial.model.add(expr>=scalar(lower,bounds))
        trial.model.add(expr<=scalar(best_score,bounds)-1)
        trial.model.minimize(expr)
        remaining=end-perf_counter()
        if remaining<=.05: break
        r=solve_to_bound(trial,trial_terms,lower,min(slice_time,remaining))
        attempts+=1; counts[r.status]=counts.get(r.status,0)+1
        if r.status=='MODEL_INVALID': raise RuntimeError('LNS MODEL_INVALID; không lưu nghiệm.')
        if r.assignments is not None:
            verify(r.assignments); point=score(r.assignments)
            if point<lower or point>=best_score: raise RuntimeError('Điểm LNS không khớp ràng buộc cải thiện.')
            if any(r.assignments[k]!=best.assignments[k] for k in best.assignments if k not in nb.cells):
                raise RuntimeError('LNS thay đổi ô đã cố định.')
            best=r; best_score=point; improved+=1
            details.append(f'{nb.kind}:{point}')
    lns_elapsed=perf_counter()-start
    note=f'LNS {kind}: {attempts} lượt, {improved} cải thiện, {lns_elapsed:.2f}s, trạng thái vùng {counts}; {before} -> {best_score}. '
    if details: note+='Các nghiệm tốt hơn: '+', '.join(details)+'. '
    # Only global proof or an attained mathematically valid global bound is a proof.
    remain=limit-(perf_counter()-start)
    if best_score==lower or remain<=.05:
        for v,b in zip(terms,best_score): ctx.model.add(v==b)
        status='PROVEN_BOUND' if best_score==lower else 'FEASIBLE'
        return best,Layer(status,SolveResult(status,best.assignments,perf_counter()-start),before,best_score,note+('Đạt cận toàn cục.' if status=='PROVEN_BOUND' else 'Hết ngân sách; khóa điểm tốt nhất.'))
    # No extra ideal probe here: spend the rest on unrestricted objective search.
    final,layer=refine_lex(ctx,best,score,terms,obj,bounds,lower,remain,0,verify)
    if layer.after==best_score and (tie_key is None or tie_key(best.assignments)<=tie_key(final.assignments)):
        final=best
    if layer.status=='UNKNOWN': layer.status='FEASIBLE'  # incumbent is known feasible
    layer.before=before
    layer.result=SolveResult(layer.status,final.assignments,perf_counter()-start)
    layer.note=note+'Toàn cục: '+layer.note
    return final,layer
