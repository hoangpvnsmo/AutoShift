"""Bounded fairness refinement; the validated stable repair remains the fallback."""
from collections import defaultdict
from threading import Event,Thread
from time import perf_counter
from ortools.sat.python import cp_model
from core.data.calendar_utils import month_days
from core.validation.credit_checks import demand_requirements
from .domain import CODE_ID,CODES,Unsupported
from .cascade_model import LinkedModel
from .cascade import inspect_candidate
from .repair_balance_context import month_record
from .year_metrics import normalized,weighted_deviation


class Workload:
    """Independent counters; outside-window workloads are fixed constants."""
    def __init__(self,frames,context):
        self.frames=frames
        records={(r['year'],r['month']):r for r in (context or {}).get('months',[])}
        # Use the exact modeled snapshot and reference, including standalone
        # repairs that explicitly chose a historical version as their baseline.
        for f in frames:records[f.data.year,f.data.month]=month_record(f.data,f.baseline,f.month_id,f.version_id)
        self.records=records;self.groups=[]
        years=sorted({f.data.year for f in frames})
        for field,annual in [('shifts',True),('shifts',False),('night',True),('night',False)]:
            scopes=[(year,None) for year in years] if annual else sorted({(f.data.year,f.data.month) for f in frames})
            for year,month in scopes:
                members=[r for (y,mo),r in records.items() if y==year and (month is None or mo==month)]
                counts=defaultdict(int);weights=defaultdict(int)
                for r in members:
                    for e in r['employees']:
                        counts[e['id']]+=e[field];weights[e['id']]+=e['weight' if field=='shifts' else 'night_weight']
                if field=='night':
                    # People who cannot do nights have neither load nor a target.
                    eligible={e['id'] for r in members for e in r['employees'] if e.get('night_eligible')}
                    weights={i:w for i,w in weights.items() if w or counts[i] or i in eligible}
                    counts={i:counts[i] for i in weights}
                label=f'{"YEAR" if annual else "MONTH"}_{field.upper()}_{year}'+(f'_{month:02d}' if month else '')
                self.groups.append(dict(label=label,year=year,month=month,field=field,counts=dict(counts),weights=normalized(dict(weights)),
                    capacity=sum(r['days'] for r in members),observed_months=len(members)))

    def counts(self,g,codes):
        values=dict(g['counts'])
        for f in self.frames:
            if f.data.year!=g['year'] or g['month'] is not None and f.data.month!=g['month']:continue
            for e in f.data.employees:
                if e.id not in values:continue
                for d in month_days(f.data.year,f.data.month):
                    k=e.id,d;old=f.baseline[k];new=codes[k]
                    values[e.id]+=int(new in ('1','2','3'))-int(old in ('1','2','3')) if g['field']=='shifts' else int(new=='1')-int(old=='1')
        return values

    def score(self,codes):
        return tuple(v for g in self.groups for v in weighted_deviation(self.counts(g,codes),g['weights']))

    def report(self,codes):
        return [dict(label=g['label'],year=g['year'],month=g['month'],field=g['field'],observed_months=g['observed_months'],
            counts=self.counts(g,codes),weights=g['weights'],deviation=list(weighted_deviation(self.counts(g,codes),g['weights']))) for g in self.groups]

    def terms(self,linked):
        out=[];model=linked.model
        for g in self.groups:
            counts=dict(g['counts']);W=sum(g['weights'].values());cap=max(1,W*g['capacity']*max(1,len(counts)))
            # Exact coverage fixes total nights (ADMIN cannot work nights).
            # Total SHIFT work changes only with ADMIN support. Express this
            # small total directly rather than summing hundreds of x variables
            # again into every absolute deviation.
            total=sum(g['counts'].values())
            if cap>=2**60:raise ValueError('Trọng số quá lớn để cân Repair an toàn.')
            for local,f in enumerate(self.frames):
                if f.data.year!=g['year'] or g['month'] is not None and f.data.month!=g['month']:continue
                days=month_days(f.data.year,f.data.month)
                demand=demand_requirements(f.data)
                if g['field']=='shifts':
                    ids={e.id for e in f.data.employees if e.group=='SHIFT'}
                    total+=sum(demand.values())-sum(c in ('1','2','3') for (i,d),c in f.baseline.items() if i in ids)-linked.credits[local].admin_total
                else:
                    total+=sum(n for (d,s),n in demand.items() if s==1)-sum(c=='1' for c in f.baseline.values())
                for e in f.data.employees:
                    if e.id not in counts:continue
                    expr=sum(linked.worked[e.id,d] if g['field']=='shifts' else linked.x[e.id,d,1] for d in days)
                    old=sum(f.baseline[e.id,d] in ('1','2','3') if g['field']=='shifts' else f.baseline[e.id,d]=='1' for d in days)
                    counts[e.id]+=expr-old
            dev=[]
            for i,w in g['weights'].items():
                v=model.new_int_var(0,cap,f'rb_dev_{g["label"]}_{i}');model.add_abs_equality(v,W*counts[i]-w*total);dev.append(v)
            out.extend([(g['label']+'_MAX',linked.maximum(dev,cap,'rb_max_'+g['label'])),(g['label']+'_SUM',sum(dev))])
        return out


def refine_repair(frames,right,flat,options,*,context=None,extra=2,deadline,emit=lambda *a:None,cancelled=lambda:False,boundary_check=None,direct_ids=()):
    """Return checked entries and a report; never publish or mutate a schedule."""
    if type(extra) is not int or not 0<=extra<=2:raise ValueError('Cân Repair cho phép thêm từ 0 đến 2 ô.')
    start=perf_counter()
    return _refine(frames,right,flat,options,context,extra,deadline,emit,cancelled,boundary_check,start,direct_ids)


def balanced_proof(proof,report):
    summary={k:report[k] for k in ('status','base_changes','selected_changes','change_limit','extra_changes','base_short_rest','selected_short_rest')}
    result={**proof,'repair_balance':summary}
    if report.get('refined') or report['status']=='IMPROVED' or report['selected_changes']!=report['base_changes']:
        same_count=report['selected_changes']==report['base_changes']
        result.update(status='FEASIBLE_BALANCED_REPAIR',objective='BOUNDED_REPAIR_BALANCE',r1_global_optimal=bool(same_count and proof.get('r1_global_optimal')),
            quality_proven=False,stable_repair_proof=proof)
        result.pop('layers',None)
        if not same_count:
            for key in ('r1_certificate','optimality_scope','reason'):result.pop(key,None)
    return result


def _refine(frames,right,flat,options,context,extra,deadline,emit,cancelled,boundary_check,start,direct_ids=()):
    base_entries,base_boundaries,base_metrics=inspect_candidate(frames,right,flat,options,direct_ids=direct_ids)
    ledger=Workload(frames,context);before=ledger.score(flat);base=base_metrics['changed_cells']
    limit=min(base+extra,options.max_changes) if options.max_changes is not None else base+extra
    report=dict(status='UNCHANGED',base_changes=base,change_limit=limit,selected_changes=base,extra_changes=0,
        base_short_rest=base_metrics['vector'][4],selected_short_rest=base_metrics['vector'][4],
        before=ledger.report(flat),after=ledger.report(flat),layers=[],seconds=0)
    report['original']=ledger.report({k:c for f in frames for k,c in f.baseline.items()})
    report['people']=list({e.id:dict(id=e.id,name=e.name) for f in frames for e in f.data.employees if e.group=='SHIFT'}.values())
    selected=dict(flat);entries,boundaries,metrics=base_entries,base_boundaries,base_metrics
    if base==0:report['status']='NO_CHANGE_NEEDED'
    elif cancelled():report['status']='CANCELLED'
    elif deadline-perf_counter()<.1:report['status']='NOT_RUN_BUDGET_EXHAUSTED'
    else:
        try:linked=LinkedModel(frames,right,options,direct_ids=direct_ids,quality=False)
        except Unsupported as exc:
            report.update(status='NOT_RUN_UNSUPPORTED',reason=str(exc),seconds=round(perf_counter()-start,3))
            metrics['repair_balance']=report
            for e in entries:e['metrics']['repair_balance']=report
            return entries,boundaries,metrics,report
        linked.model.add(linked.groups[0][0]<=limit)
        # Fairness must not buy more resource use or new short rests than the
        # stable repair. This applies even if a user disabled a soft R tier.
        for n in (0,1):linked.model.add(linked.groups[3][n]<=base_metrics['vector'][3][n])
        linked.model.add(linked.groups[4][0]<=base_metrics['vector'][4])
        terms=ledger.terms(linked)+[('CHANGES',linked.groups[0][0]),('MONTHS_CHANGED',linked.months_changed)]
        terms.extend((label,t) for label,t in linked.terms if label not in ('R1','MONTHS_CHANGED'))
        # Complete the known feasible hint once, including continuity, change,
        # rest and fairness auxiliaries. A code-only hint leaves thousands of
        # auxiliary variables unresolved before the first incumbent is found.
        warm=linked.model.clone();warm.clear_objective();warm.clear_hints()
        for k,c in flat.items():warm.add(warm.get_int_var_from_proto_index(linked.values[k].index)==CODE_ID[c])
        warm_solver=cp_model.CpSolver();warm_solver.parameters.num_search_workers=1
        warm_solver.parameters.max_time_in_seconds=max(.01,min(2,deadline-perf_counter()))
        warm_status=warm_solver.solve(warm)
        report['warm_start']=dict(status=warm_solver.status_name(warm_status),seconds=round(warm_solver.wall_time,3),variables=len(warm.proto.variables))
        if warm_status in (cp_model.OPTIMAL,cp_model.FEASIBLE):
            check=[int(warm_solver.value(t)) for label,t in terms[:len(before)]]
            if check!=list(before):raise ValueError(f'Điểm cân Repair của nghiệm nền không khớp: {check} != {before}')
        complete_hint=[warm_solver.value(warm.get_int_var_from_proto_index(i)) for i in range(len(warm.proto.variables))] if warm_status in (cp_model.OPTIMAL,cp_model.FEASIBLE) else None
        def key(c,m):
            return ledger.score(c)+(m['changed_cells'],m['changed_months'])+tuple(v for j in (1,2,3,4,5,6) if f'R{j+1}' not in options.disabled_layers for v in (m['vector'][j] if isinstance(m['vector'][j],list) else [m['vector'][j]]))
        point=list(before)+[base,base_metrics['changed_months']]
        point.extend(v for j in range(1,7) if f'R{j+1}' not in options.disabled_layers for v in (base_metrics['vector'][j] if isinstance(base_metrics['vector'][j],list) else [base_metrics['vector'][j]]))
        for n,(label,term) in enumerate(terms):
            if cancelled():report['status']='CANCELLED';break
            remaining=deadline-perf_counter()
            if remaining<.05:break
            if isinstance(term,int) or point[n]==0:
                linked.model.add(term==point[n]);continue
            linked.model.minimize(term);linked.model.add(term<=point[n]);linked.model.clear_hints()
            if complete_hint is not None:
                for i,value in enumerate(complete_hint):linked.model.add_hint(linked.model.get_int_var_from_proto_index(i),value)
            else:
                for k,c in selected.items():linked.model.add_hint(linked.values[k],CODE_ID[c])
            solver=cp_model.CpSolver();solver.parameters.num_search_workers=8
            # The complete hint is already feasible. Avoid consuming a short
            # pass in presolve before this incumbent can be recorded.
            solver.parameters.cp_model_presolve=False
            solver.parameters.random_seed=2027
            slots=max(1,len(before)-n) if n<len(before) else max(1,len(terms)-n)
            solver.parameters.max_time_in_seconds=max(.01,min(remaining,max(.25,remaining*.8/slots)))
            stop=Event()
            def watch():
                while not stop.wait(.25):
                    if cancelled():solver.stop_search();return
            thread=Thread(target=watch,daemon=True);thread.start()
            try:status=solver.solve(linked.model)
            finally:stop.set();thread.join()
            if status==cp_model.MODEL_INVALID:raise ValueError('Cân Repair MODEL_INVALID: '+solver.solution_info())
            if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):
                report['layers'].append(dict(layer=label,status=solver.status_name(status)));break
            proposed={k:CODES[solver.value(v)-1] for k,v in linked.values.items()}
            try:
                pe,pb,pm=inspect_candidate(frames,right,proposed,options,direct_ids=direct_ids)
                if boundary_check is not None:boundary_check(proposed)
            except ValueError as exc:
                report['layers'].append(dict(layer=label,status='REJECTED_VALIDATOR',reason=str(exc)));break
            actual=list(ledger.score(proposed))+[pm['changed_cells'],pm['changed_months']]
            actual.extend(v for j in range(1,7) if f'R{j+1}' not in options.disabled_layers for v in (pm['vector'][j] if isinstance(pm['vector'][j],list) else [pm['vector'][j]]))
            expected=[int(solver.value(t)) for name,t in terms]
            if actual!=expected or actual[:n]!=point[:n] or actual[n]>point[n]:raise ValueError('Vector cân Repair không khớp bộ tính độc lập.')
            if pm['changed_cells']>limit or pm['vector'][4]>base_metrics['vector'][4] or any(a>b for a,b in zip(pm['vector'][3],base_metrics['vector'][3])):
                raise ValueError('Cân Repair vượt giới hạn thay đổi, nghỉ hoặc nguồn lực.')
            accepted=key(proposed,pm)<key(selected,metrics)
            if accepted:
                selected,entries,boundaries,metrics,point=proposed,pe,pb,pm,actual
                complete_hint=[solver.value(linked.model.get_int_var_from_proto_index(i)) for i in range(len(linked.model.proto.variables))]
            linked.model.add(term==point[n])
            report['layers'].append(dict(layer=label,status=solver.status_name(status),value=point[n],accepted=accepted))
            emit('Cân Repair:',label,solver.status_name(status),point[n])
        if report['status']!='CANCELLED' and ledger.score(selected)<before:report['status']='IMPROVED'
        # Keep the validated stable repair when fairness did not improve.
        elif selected!=flat:
            selected,entries,boundaries,metrics=dict(flat),base_entries,base_boundaries,base_metrics
    report.update(selected_changes=metrics['changed_cells'],extra_changes=max(0,metrics['changed_cells']-base),
        selected_short_rest=metrics['vector'][4],after=ledger.report(selected),refined=selected!=flat,seconds=round(perf_counter()-start,3))
    metrics['repair_balance']=report
    for e in entries:e['metrics']['repair_balance']=report
    return entries,boundaries,metrics,report
