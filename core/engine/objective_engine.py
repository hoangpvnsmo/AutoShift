"""Unified objective engine: one ordered pipeline, immutable options, structured result."""
from core.optimization.objective_alns import refine_alns
from dataclasses import dataclass, replace
from core.engine.checkpoint import Checkpoint, active_vector, prefix_length, reusable_prefix
from core.engine.engine_config import EngineOptions, STAGES, KEYS
from core.optimization.objective_night import Layer,flag,score_workforce
from core.optimization.objective import lock_workforce
from core.optimization.solver import solve,SolveResult
from core.optimization.objective_days import refine_bounded
from core.optimization.objective_day_v2 import refine_day
from core.optimization.objective_rest import refine_rest
from core.optimization.objective_lex_v2 import refine_lex,scalar
from core.objectives.soft_constraints import add_shift_balance
from core.objectives.night_constraints import add_night_balance
from core.objectives.night_checks import night_rows,night_score,night_lower_bound
from core.objectives.quality_checks import shift_balance_rows,shift_balance_score
from core.objectives.day_balance_checks import day_sets,day_balance_rows,e01_lower_bound
from core.objectives.day_balance_v2 import day_score_v2,day_bound,add_day_balance_v2
from core.objectives.rest_checks import rest_windows,rest_rows,rest_scores
from core.objectives.rest_constraints import add_rest_balance
from core.objectives.rest_bound_v2 import weekend_lower_v2
from core.objectives.rotation_checks import rotation_rows,rotation_score
from core.objectives.flexible_rotation import add_flexible_rotation, flexible_rows, flexible_score
from core.objectives.preference_checks import preference_rows,preference_score,preference_lower,validate_preferences
from core.objectives.preference_constraints import add_preferences
from core.validation.admin_checks import check_admin_credits
from core.validation.credit_checks import check_staffing
from core.validation.sequence_checks import check_sequences
from core.validation.transition_checks import check_transitions

@dataclass
class ObjectiveResult:
    baseline: SolveResult
    selected: SolveResult
    workforce: tuple | None = None
    e01: Layer | None = None
    e02: Layer | None = None
    holiday: Layer | None = None
    weekend: Layer | None = None
    rest_a: Layer | None = None
    rest_b: Layer | None = None
    rotation: Layer | None = None
    preferences: Layer | None = None
    rest_b_lower: tuple | None = None
    preference_lower: tuple | None = None

    @property
    def statuses(self):
        return (self.baseline.status, *(getattr(self,k).status if getattr(self,k) is not None else 'NOT_RUN' for k in KEYS))

    @property
    def proven_prefix(self):
        return prefix_length(self.statuses)

    @property
    def proven(self):
        return self.selected.assignments is not None and self.proven_prefix == len(STAGES)

@dataclass(frozen=True)
class ObjectiveRun:
    quality: ObjectiveResult
    old_valid: bool
    old_scores: tuple | None
    old_prefix: int


def active_layers(data):
    return tuple(s.flag is None or flag(data,s.flag) for s in STAGES)


def scorers(ctx,data,prefs):
    w=rest_windows(data,ctx.days)
    def rest(a,j): return rest_scores(rest_rows(data,ctx.days,a,w),len(w.weekends))[j]
    return (lambda a:score_workforce(ctx,data,a),
            lambda a:shift_balance_score(shift_balance_rows(data,ctx.days,a)),
            lambda a:night_score(night_rows(data,ctx.days,a)),
            lambda a:day_score_v2(day_balance_rows(data,ctx.days,a),'holiday'),
            lambda a:day_score_v2(day_balance_rows(data,ctx.days,a),'weekend'),
            lambda a:rest(a,0),lambda a:rest(a,1),
            lambda a:flexible_score(flexible_rows(data,ctx.days,a)),
            lambda a:preference_score(preference_rows(data,ctx.days,a,prefs)))


def scores_of(ctx,data,prefs,a): return tuple(fn(a) for fn in scorers(ctx,data,prefs))


def hard_errors(ctx,data,a):
    if set(a)!=set(ctx.worked): return ['Thiếu/thừa ID hoặc ngày.']
    if any(type(s) is not int or s not in (0,1,2,3) for s in a.values()): return ['Mã ca sai.']
    errors=list(check_admin_credits(data,ctx.days,a)[0]); errors+=check_staffing(data,ctx.days,a)
    errors+=check_sequences(data,ctx.days,a)[0]; errors+=check_transitions(data,ctx.days,a)[0]
    return errors


def validate_incumbent(ctx,data,a,limit=10):
    errors=hard_errors(ctx,data,a)
    if errors: return False,'; '.join(errors[:3])
    # Independently verify all current model constraints by fixing x in a clone.
    m=ctx.model.clone(); m.clear_objective(); m.clear_hints()
    var=lambda v:m.get_int_var_from_proto_index(v.index)
    trial=replace(ctx,model=m,x={k:var(v) for k,v in ctx.x.items()},worked={k:var(v) for k,v in ctx.worked.items()})
    for (i,d,s),v in trial.x.items(): m.add(v==int(a[i,d]==s))
    r=solve(trial,time_limit=limit)
    if r.status=='MODEL_INVALID': raise RuntimeError('Mô hình kiểm tra checkpoint không hợp lệ.')
    return r.assignments==a, f'Kiểm tra mô hình cố định: {r.status} ({r.wall_time:.2f}s)'


def partial_hint(ctx,a):
    ctx.model.clear_hints()
    for (i,d,s),v in ctx.x.items():
        if (i,d) in a: ctx.model.add_hint(v,int(a[i,d]==s))
    for k,v in ctx.worked.items():
        if k in a: ctx.model.add_hint(v,int(a[k]!=0))


def choose_on_tie(old,new,score,all_scores,active):
    # Compare downstream scores only when the current tier is equal.
    if new is old or score(old.assignments)!=score(new.assignments): return new
    vector=lambda r: tuple(fn(r.assignments) for fn,on in zip(all_scores,active) if on)
    return new if vector(new)<vector(old) else old


def solve_objectives(ctx,data,credit,demand,preferences=(),*,options=None,saved=None,hashes=None,emit=print):
    """Optimize a freshly built model. Never reuse a context already locked by a run."""
    opts=options if options is not None else EngineOptions()
    if saved is not None and hashes is None: raise ValueError('Cần fingerprints khi truyền checkpoint.')
    fresh=opts.fresh
    prefs=validate_preferences(data,tuple(preferences)); score=scorers(ctx,data,prefs); active=active_layers(data)
    valid=False; old_scores=None; prefix=0
    if saved is not None:
        valid,note=validate_incumbent(ctx,data,saved.assignments,opts.validation_time)
        emit('Checkpoint:', 'HỢP LỆ' if valid else 'KHÔNG dùng làm dự phòng', '|',note)
        if valid:
            old_scores=tuple(fn(saved.assignments) for fn in score)
            prefix=reusable_prefix(saved,*hashes,old_scores)
            emit('Số tầng chứng minh được tái sử dụng:',prefix if not fresh else 0)
        if not fresh: partial_hint(ctx,saved.assignments)
    if fresh: ctx.model.clear_hints()
    old=SolveResult('FEASIBLE',saved.assignments,0) if valid and not fresh else None
    reuse=prefix if old is not None else 0
    if reuse:
        base=SolveResult('OPTIMAL',old.assignments,0)
        emit('Stage 3: dùng chứng minh checkpoint, không giải lại.')
    else:
        if old is not None:
            n=sum(e.group=='SHIFT' for e in data.employees); a=len(data.employees)-n; D=len(ctx.days)
            bounds=(n*D,a*D,D,n*n*D,a*a*D)
            ctx.model.add(credit.objective<=scalar(score[0](old.assignments),bounds))
        r=solve(ctx,time_limit=opts.base_time)
        if r.status=='MODEL_INVALID': raise RuntimeError('Stage 3 MODEL_INVALID.')
        if old is not None and r.status=='INFEASIBLE': raise RuntimeError('Stage 3 INFEASIBLE dù checkpoint đã hợp lệ.')
        if old is not None and (r.assignments is None or score[0](old.assignments)<=score[0](r.assignments)):
            if r.status=='OPTIMAL' and score[0](old.assignments)<score[0](r.assignments): raise RuntimeError('Stage 3 tối ưu kém hơn checkpoint.')
            base=SolveResult('OPTIMAL' if r.status=='OPTIMAL' else 'FEASIBLE',old.assignments,r.wall_time)
            emit('Stage 3: giữ checkpoint (không kém nghiệm mới).')
        else: base=r
    q=ObjectiveResult(base,base)
    if base.assignments is None: return ObjectiveRun(q,valid,old_scores,prefix)
    q.workforce=score[0](base.assignments); lock_workforce(ctx,credit,q.workforce)
    locked=[(0,q.workforce)]; w=rest_windows(data,ctx.days); quality=None; rest_model=None
    dates=day_sets(data,ctx.days)
    def verify(a):
        for j,v in locked:
            if score[j](a)!=v: raise RuntimeError(f'Thay đổi điểm ưu tiên đã khóa: tầng {j}.')
    for j,spec in enumerate(STAGES[1:],1):
        key=spec.key; limit=opts.budget_for(key)
        before=score[j](q.selected.assignments); prior=q.selected
        if not active[j]:
            layer=Layer('DISABLED',None,before,before,'Tắt trong cấu hình.')
        elif j in (3,4) and not dates[j-3]:
            layer=Layer('NO_DAYS',None,before,before,'Không có ngày thuộc nhóm.')
        elif key=='preferences' and not prefs:
            q.preference_lower=(0,)
            layer=Layer('NO_PREFERENCES',None,before,before,'Không có nguyện vọng mềm.')
        else:
            if key=='e01':
                quality=add_shift_balance(ctx,data); terms=(quality.worst_gap,quality.total_gap); obj=quality.objective
                lower=e01_lower_bound(data,ctx.days,demand,q.workforce)
                refine=lambda:refine_bounded(ctx,prior,score[j],terms,obj,lower,limit,verify)
            elif key=='e02':
                t=add_night_balance(ctx,data,quality.counts if quality else None); terms=(t.worst_gap,t.total_gap); obj=t.objective
                lower=night_lower_bound(data,demand)
                refine=lambda:refine_bounded(ctx,prior,score[j],terms,obj,lower,limit,verify)
            elif key in ('holiday','weekend'):
                ds=dates[j-3]; t,terms,obj=add_day_balance_v2(ctx,data,ds,key)
                bound=day_bound(data,ds,demand,q.workforce[1]); lower=bound.lower
                refine=lambda:refine_day(ctx,prior,score[j],t,terms,obj,bound,limit,opts.ideal_time,verify)
            elif key=='rest_a':
                rest_model=add_rest_balance(ctx,data); terms=rest_model.terms_a; obj=rest_model.obj_a; lower=(0,0,0)
                refine=lambda:refine_rest(ctx,prior,score[j],terms,obj,lower,limit,opts.ideal_time,verify)
            elif key=='rest_b':
                terms=rest_model.terms_b; obj=rest_model.obj_b
                lower=q.rest_b_lower=weekend_lower_v2(data,ctx.days,w,demand,q.workforce[1],q.weekend)
                n=sum(e.group=='SHIFT' for e in data.employees); W=len(w.weekends); D=len(ctx.days)
                refine=lambda:refine_lex(ctx,prior,score[j],terms,obj,(n,n*W,W,D),lower,limit,opts.ideal_time,verify)
            elif key=='rotation':
                t=add_flexible_rotation(ctx,data); terms=t.terms; obj=t.objective; lower=(0,0,0,0)
                refine=lambda:refine_alns(ctx,data,prior,score[j],terms,obj,t.bounds,lower,limit,opts.ideal_time,verify,kind="flexible",fraction=opts.lns_fraction,slice_time=opts.lns_slice,seed=opts.lns_seed,adaptive=opts.adaptive,stall_limit=opts.stall_limit,tie_key=lambda a:active_vector(tuple(fn(a) for fn in score),active))
            else:
                terms,obj,bounds=add_preferences(ctx,data,prefs); lower=q.preference_lower=preference_lower(data,prefs)
                refine=lambda:refine_alns(ctx,data,prior,score[j],terms,obj,bounds,lower,limit,opts.ideal_time,verify,kind="preferences",prefs=prefs,fraction=opts.lns_fraction,slice_time=opts.lns_slice,seed=opts.lns_seed,adaptive=opts.adaptive,stall_limit=opts.stall_limit,tie_key=lambda a:active_vector(tuple(fn(a) for fn in score),active))
            if j<reuse:
                if before!=saved.scores[j]: raise RuntimeError('Điểm checkpoint không khớp tầng đã chứng minh.')
                for v,b in zip(terms,before): ctx.model.add(v==b)
                layer=Layer('OPTIMAL',None,before,before,'Tái sử dụng chứng minh checkpoint; khóa điểm, không khóa lịch.')
            else:
                new,layer=refine()
                q.selected=choose_on_tie(prior,new,score[j],score,active)
                if q.selected is prior and new is not prior: layer.note+=' Giữ lịch nền khi điểm bằng nhau.'
            locked.append((j,layer.after))
        setattr(q,key,layer)
        verify(q.selected.assignments)
    for j,key in enumerate(KEYS,1):
        layer=getattr(q,key)
        if not active[j]: layer.after=score[j](q.selected.assignments)
    actual=tuple(fn(q.selected.assignments) for fn in score)
    if valid and not fresh and active_vector(actual,active)>active_vector(old_scores,active):
        raise RuntimeError('Nghiệm mới kém checkpoint hợp lệ: không được lưu.')
    return ObjectiveRun(q,valid,old_scores,prefix)


def make_checkpoint(ctx,data,prefs,q,hashes):
    if q.selected.assignments is None: raise ValueError("Không có nghiệm để lưu.")
    return Checkpoint(*hashes,dict(q.selected.assignments),scores_of(ctx,data,prefs,q.selected.assignments),q.proven_prefix)
