"""Expand a linked repair window; validate every candidate independently."""
from dataclasses import dataclass, field
from time import perf_counter
from threading import Event, Thread
from ortools.sat.python import cp_model
from core.data.calendar_utils import month_days
from validator.quality import score_vector
from .cascade_model import LinkedModel, previous_with
from .domain import CODES, CODE_ID, changed_cells
from .validation import validate_month, validate_repair, short_events, boundary_report


@dataclass
class CascadeResult:
    status: str = 'UNKNOWN'
    entries: list = field(default_factory=list)
    boundaries: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    proof: dict = field(default_factory=dict)
    attempts: list = field(default_factory=list)


def inspect_candidate(frames, right, flat, options, *, root_fresh=False, direct_ids=()):
    """Independent full-code checks, including the fixed right frontier."""
    old = {k:c for f in reversed(frames) for k,c in f.data.previous.items()}
    old.update({k:c for f in frames for k,c in f.baseline.items()})
    entries=[];boundaries=[];people=set();changed_months=0
    vector=[0,0,0,[0,0],0,0,0,[]]
    for j,f in enumerate(frames):
        data=previous_with(f.data,flat)
        codes={k:flat[k] for k in flat if k[1].year==data.year and k[1].month==data.month}
        allowed={d for d in month_days(data.year,data.month) if j or (
            (options.allowed_from is None or d>=options.allowed_from) and
            (options.allowed_until is None or d<=options.allowed_until))}
        locks=set(f.locks)
        if f.status=='ACTUAL':locks.update(f.baseline)
        if f.baseline:
            metrics,report=validate_repair(data,f.baseline,codes,direct_ids=direct_ids,as_of=options.as_of,
                locks=locks,allowed=allowed,published=f.status=='PUBLISHED',annual=f.annual,
                prefs=f.prefs,work_policy=f.work_policy,
                no_admin_increase=options.no_admin_increase if not(root_fresh and j==0) else False,
                no_overtime_increase=options.no_overtime_increase if not(root_fresh and j==0) else False)
            # The before-history is the actual old chain, not the candidate chain.
            metrics['new_short_rest']=len(short_events(data,flat)-short_events(data,old))
            metrics['vector'][4]=metrics['new_short_rest']
        else:
            report=validate_month(data,codes,f.prefs,f.work_policy)
            metrics=None
        if not(root_fresh and j==0):
            v=metrics['vector'];vector[0]+=v[0];vector[2]+=v[2]
            vector[3]=[a+b for a,b in zip(vector[3],v[3])]
            for k in (4,5,6):vector[k]+=v[k]
            people.update(c['employee_id'] for c in metrics['changes'] if c['employee_id'] not in direct_ids)
            changed_months+=int(bool(v[0]))
        q=[list(row) for row in score_vector(report)];vector[7].append(q)
        entry=dict(frame=f,data=data,codes=codes,report=report,metrics=metrics,quality=q)
        if entries:
            last=entries[-1]
            b=boundary_report(last['data'],last['codes'],data,codes,prefs=f.prefs,work_policy=f.work_policy)
            if b['errors']:raise ValueError('Validator từ chối biên trong gói sửa liên kết.')
            boundaries.append(b)
        entries.append(entry)
    vector[1]=len(people)
    if options.max_changes is not None and vector[0]>options.max_changes:raise ValueError('Vượt tổng số ô cho phép đổi.')
    if right:
        last=entries[-1]
        b=boundary_report(last['data'],last['codes'],right.fixed_data or right.data,right.baseline,
            prefs=right.fixed_prefs,work_policy=right.fixed_policy)
        if b['errors']:raise ValueError('Validator từ chối biên với tháng kế tiếp đang giữ nguyên.')
        boundaries.append(b)
    return entries,boundaries,dict(vector=vector,changed_cells=vector[0],changed_months=changed_months,
        other_people=vector[1],root_counted=not root_fresh)


def _key(metrics,options,active):
    v=metrics['vector'];out=[v[0],metrics['changed_months']]
    out.extend(tuple(v[j]) if isinstance(v[j],list) else v[j] for j in range(1,7) if f'R{j+1}' not in options.disabled_layers)
    if 'R8' not in options.disabled_layers:
        out.extend(tuple(v[7][j][tier]) for tier in range(9) for j in range(len(active)) if active[j][tier])
    return tuple(out)


def solve_cascade(frames, frontier, options, *, root_fresh=False, direct_ids=(), minimum_size=1, emit=lambda *a:None, cancelled=lambda:False):
    deadline=perf_counter()+options.budget;best=CascadeResult();uncertain=False
    for size in range(minimum_size,len(frames)+1):
        if cancelled():best.status='CANCELLED';return best
        remaining=deadline-perf_counter()
        if remaining<=.02:break
        right=frames[size] if size<len(frames) else frontier
        emit(f'Liên kết {size} tháng; tháng sau giữ nguyên: '+(f'{right.data.year}-{right.data.month:02d}' if right else 'chưa có lịch'))
        linked=LinkedModel(frames[:size],right,options,root_fresh=root_fresh,direct_ids=direct_ids)
        # In FAST use a small window first, reserving time to expand on failure.
        end=deadline if size==len(frames) else min(deadline,perf_counter()+max(.2,remaining*.45))
        incumbent=None;layers=[];outcome='UNKNOWN';checked=None
        for n,(label,term) in enumerate(linked.terms):
            if cancelled():best.status='CANCELLED';return best
            if incumbent is not None and isinstance(term,int):
                layers.append(dict(layer=label,status='OPTIMAL',value=term,bound=term))
                continue
            remaining=end-perf_counter()
            if remaining<=.01:break
            linked.model.minimize(term);linked.model.clear_hints()
            hint=incumbent or {k:c for j,f in enumerate(frames[:size]) if j or not root_fresh for k,c in f.baseline.items()}
            for k,c in hint.items():linked.model.add_hint(linked.values[k],CODE_ID[c])
            solver=cp_model.CpSolver();solver.parameters.num_search_workers=8
            solver.parameters.max_time_in_seconds=max(.01,remaining if n==0 else min(remaining,max(2,remaining/(len(linked.terms)-n))))
            stop=Event()
            def watch_cancel():
                while not stop.wait(.25):
                    if cancelled():solver.stop_search();return
            watcher=Thread(target=watch_cancel,daemon=True);watcher.start()
            try:status=solver.solve(linked.model)
            finally:stop.set();watcher.join()
            outcome=solver.status_name(status)
            if status==cp_model.MODEL_INVALID:raise ValueError('Linked MODEL_INVALID: '+solver.solution_info())
            if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
                break
            candidate={k:CODES[solver.value(v)-1] for k,v in linked.values.items()}
            checked=inspect_candidate(frames[:size],right,candidate,options,root_fresh=root_fresh,direct_ids=direct_ids)
            entries,boundaries,metrics=checked
            expected=[]
            for j,group in enumerate(linked.groups):
                row=[int(solver.value(t)) for t in group];expected.append(row if j==3 else row[0])
            expected.append([[list(map(lambda t:int(solver.value(t)),group)) for group in quality] for quality in linked.qualities])
            if metrics['vector']!=expected or metrics['changed_months']!=solver.value(linked.months_changed):
                raise ValueError('Vector liên kết không khớp bộ tính độc lập.')
            incumbent=candidate;value=int(solver.value(term));linked.model.add(term==value)
            layers.append(dict(layer=label,status=outcome,value=value,bound=solver.best_objective_bound))
            emit(f'Liên kết {size}: {label} {outcome}, điểm {value}')
        attempt=dict(months=size,status=outcome,layers=layers);best.attempts.append(attempt)
        if incumbent is None:
            uncertain|=outcome=='UNKNOWN'
            continue
        entries,boundaries,metrics=checked
        proof=dict(status='FEASIBLE_WINDOW',r1_global_optimal=False,quality_proven=False,
            modeled_months=size,available_months=len(frames),right_fixed=right.month_id if right else None,layers=layers,
            objective='FUTURE_MONTHS' if root_fresh else 'ALL_MODELED_MONTHS')
        if metrics['changed_cells']==0 or (size==len(frames) and layers and layers[0]['status']=='OPTIMAL'):
            proof.update(status='R1_OPTIMAL_WITHIN_HORIZON',r1_global_optimal=True,
                optimality_scope=[f.month_id for f in frames],r1_certificate=layers[0],
                reason='ZERO_LOWER_BOUND' if metrics['changed_cells']==0 else 'FULL_HORIZON_OPTIMAL')
        if not best.entries or _key(metrics,options,linked.active)<_key(best.metrics,options,linked.active[:len(best.entries)]):
            best.entries,best.boundaries,best.metrics,best.proof=entries,boundaries,metrics,proof
        elif proof['r1_global_optimal'] and best.metrics['changed_cells']==metrics['changed_cells']:
            best.proof.update(status=proof['status'],r1_global_optimal=True,optimality_scope=proof['optimality_scope'],
                reason=proof['reason'],r1_certificate=proof['r1_certificate'])
        if options.mode=='FAST' or metrics['changed_cells']==0:break
    best.status='FEASIBLE' if best.entries else 'INFEASIBLE' if not uncertain and best.attempts and best.attempts[-1]['months']==len(frames) and best.attempts[-1]['status']=='INFEASIBLE' else 'UNKNOWN'
    if cancelled():best.status='CANCELLED'
    return best
