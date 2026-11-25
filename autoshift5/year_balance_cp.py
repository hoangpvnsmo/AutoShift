"""Small linked CP windows refine the independently scored whole-year ledger."""
from dataclasses import replace
from time import perf_counter
from threading import Event,Thread
from ortools.sat.python import cp_model
from core.data.calendar_utils import month_days
from core.validation.credit_checks import target_credit
from .cascade_model import LinkedModel,previous_with
from .cascade import inspect_candidate
from .repair import indicator_difference
from .domain import CODE_ID,CODES
from .year_metrics import SHIFT


def add_terms(linked,ctx,codes,indices):
    rows,monthly,full,resource=ctx.counts(codes);model=linked.model
    counts={name:{i:r[name] for i,r in rows.items()} for name in ctx.weights}
    resources=list(resource);spread=sum(max(monthly[e.id,j] for e in f.data.employees if e.group=='SHIFT')-
        min(monthly[e.id,j] for e in f.data.employees if e.group=='SHIFT') for j,f in enumerate(ctx.frames))
    for local,j in enumerate(indices):
        frame=ctx.frames[j];data=frame.data;days=month_days(data.year,data.month);credit=linked.credits[local]
        for e in data.employees:
            current=sum(codes[e.id,d] in SHIFT for d in days)
            expr=sum(linked.worked[e.id,d] for d in days)
            if e.group=='SHIFT':
                counts['total_shifts'][e.id]+=expr-current
                counts['weekend_shifts'][e.id]+=sum(linked.worked[e.id,d] for d in days if d.weekday()>=5)-sum(codes[e.id,d] in SHIFT for d in days if d.weekday()>=5)
                resources[0]+=credit.excess[e.id]-max(0,monthly[e.id,j]-target_credit(data,e))
            else:resources[1]+=expr-current
        old=[monthly[e.id,j] for e in data.employees if e.group=='SHIFT']
        spread+=credit.shift_spread-(max(old)-min(old))
    def eq(i,day,code):
        if (i,day) not in linked.values:return int(codes.get((i,day),ctx.extra.get((i,day)))==code)
        if code in SHIFT:return linked.x[i,day,int(code)]
        return 1-indicator_difference(model,linked.values[i,day],CODE_ID[code],f'year_eq_{i}_{day}_{code}')
    for n,(i,sat,sun,pairs) in enumerate(ctx.events):
        if not any((i,d) in linked.values for d in {sat,sun}|{d for d,s in pairs}):continue
        value=linked.both([eq(i,sat,'OFF'),eq(i,sun,'OFF')]+[1-eq(i,d,s) for d,s in pairs],f'year_full_{i}_{sun}')
        counts['full_weekends'][i]+=value-full[n]
    out=[('OVER',resources[0]),('ADMIN',resources[1])]
    def deviation(name):
        weights=ctx.weights[name];W=sum(weights.values());total=sum(counts[name].values());dev=[]
        cap=max(1,W*len(ctx.days)*len(weights))
        if cap>=2**60:raise ValueError('Trọng số công chuẩn quá lớn để tối ưu cân năm an toàn.')
        for i,w in weights.items():
            v=model.new_int_var(0,cap,f'year_dev_{name}_{i}');model.add_abs_equality(v,W*counts[name][i]-w*total);dev.append(v)
        worst=linked.maximum(dev,cap,'year_worst_'+name)
        return worst,sum(dev)
    for name in ('total_shifts','weekend_shifts'):
        out.extend((name,term) for term in deviation(name))
    weights=ctx.weights['full_weekends'];W=sum(weights.values());rates=[]
    for i,w in weights.items():
        if not w:continue
        total_full=model.new_int_var(0,sum(event[0]==i for event in ctx.events),f'year_rest_count_{i}')
        model.add(total_full==counts['full_weekends'][i])
        rate=model.new_int_var(0,ctx.full_ceiling,f'year_rest_rate_{i}')
        model.add_division_equality(rate,W*total_full,w);rates.append(rate)
    minimum=model.new_int_var(0,ctx.full_ceiling,'year_rest_minimum')
    if rates:model.add_min_equality(minimum,rates)
    else:model.add(minimum==0)
    out.append(('REST_MIN',ctx.full_ceiling-minimum))
    model.add(sum(counts['full_weekends'].values())>=ctx.full_floor)
    out.extend(('REST_GAP',term) for term in deviation('full_weekends'))
    out.extend([('REST_TOTAL',-sum(counts['full_weekends'].values())),('MONTH_CREDIT',spread)])
    changed=sum(c!=ctx.original[k] for k,c in codes.items() if k not in linked.values)
    changed+=sum(indicator_difference(model,v,CODE_ID[ctx.original[k]],f'year_changed_{k[0]}_{k[1]}') for k,v in linked.values.items())
    if linked.options.max_changes is not None:model.add(changed<=linked.options.max_changes)
    out.append(('CHANGES',changed))
    model.add(resources[0]<=resource[0]);model.add(resources[1]<=resource[1])
    return out


def refine_windows(ctx,codes,options,*,deadline,window_months,emit,cancelled):
    selected=dict(codes);attempts=[];size=min(window_months,len(ctx.frames))
    mutable=[j for j,f in enumerate(ctx.frames) if f.status not in ('ACTUAL','PUBLISHED') and any(k not in set(f.locks) for k in f.baseline)]
    starts=sorted({max(0,min(j,len(ctx.frames)-size)) for j in mutable})
    # Cover different portions of the year early, then overlapping windows.
    starts=starts[::size]+[j for j in starts if j not in starts[::size]]
    for turn,start in enumerate(starts):
        if cancelled() or deadline-perf_counter()<3:break
        indices=list(range(start,min(len(ctx.frames),start+size)))
        frames=[replace(ctx.frames[j],data=previous_with(ctx.frames[j].data,selected),baseline={k:selected[k] for k in ctx.frames[j].baseline}) for j in indices]
        right=ctx.frames[indices[-1]+1] if indices[-1]+1<len(ctx.frames) else ctx.frontier
        if right:right=replace(right,baseline={k:selected.get(k,c) for k,c in right.baseline.items()})
        begin=perf_counter();linked=LinkedModel(frames,right,options,quality=False,repair_terms=False)
        terms=add_terms(linked,ctx,selected,indices);point=ctx.report(selected)['score'];incumbent=dict(selected)
        end=min(deadline,perf_counter()+max(3,(deadline-perf_counter())/min(3,max(1,len(starts)-turn))))
        layers=[]
        for index,(label,term) in enumerate(terms):
            if cancelled() or end-perf_counter()<.05:break
            # Global zero and equal-exposure arithmetic bounds need no re-proof.
            bound=None
            if index<2:bound=0
            if 2<=index<=5 and point[1]==0:
                name='total_shifts' if index<4 else 'weekend_shifts';weights=ctx.weights[name]
                if weights and len(set(weights.values()))==1 and min(weights.values())>0:
                    report=ctx.report(incumbent);total=sum(r[name] for r in report['employees']);n=len(weights);r=total%n
                    bound=(max(r,n-r) if r else 0) if index%2==0 else 2*r*(n-r)
            if isinstance(term,int) or bound is not None and point[index]==bound:
                linked.model.add(term==point[index]);layers.append(dict(layer=label,status='BOUND_OR_FIXED',value=point[index]));continue
            linked.model.minimize(term);linked.model.add(term<=point[index]);linked.model.clear_hints()
            for k,v in linked.values.items():linked.model.add_hint(v,CODE_ID[incumbent[k]])
            solver=cp_model.CpSolver();solver.parameters.num_search_workers=8
            solver.parameters.max_time_in_seconds=max(.01,min(end-perf_counter(),max(2,(end-perf_counter())*.7)))
            stop=Event()
            def watch():
                while not stop.wait(.25):
                    if cancelled():solver.stop_search();return
            thread=Thread(target=watch,daemon=True);thread.start()
            try:status=solver.solve(linked.model)
            finally:stop.set();thread.join()
            if status==cp_model.MODEL_INVALID:raise ValueError('Cân năm MODEL_INVALID: '+solver.solution_info())
            if status not in (cp_model.FEASIBLE,cp_model.OPTIMAL):break
            proposed={**incumbent,**{k:CODES[solver.value(v)-1] for k,v in linked.values.items()}}
            actual=ctx.report(proposed)['score'];expected=[int(solver.value(t)) for name,t in terms]
            if actual!=expected:raise ValueError('Vector CP cân năm không khớp tính độc lập.')
            if actual[:index]!=point[:index] or actual[index]>point[index]:
                raise ValueError('CP cân năm thay đổi tầng đã khóa hoặc làm xấu tầng đang tối ưu.')
            # A solver tie may arbitrarily worsen later tiers. Keep the known
            # feasible incumbent while still locking this achieved tier value.
            accepted=actual<point
            if accepted:
                try:inspect_candidate(ctx.frames,ctx.frontier,proposed,options)
                except ValueError as exc:
                    if 'B_WORK_TIME' not in str(exc):raise
                    layers.append(dict(layer=label,status='REJECTED_VALIDATOR',message=str(exc)))
                    emit('Vùng cân năm bị bộ kiểm tra giờ phi ca từ chối; giữ phương án trước.')
                    break
                incumbent=proposed;point=actual
            linked.model.add(term==point[index])
            layers.append(dict(layer=label,status=solver.status_name(status),value=point[index],bound=solver.best_objective_bound,accepted=accepted))
            emit('Cân năm CP vùng',start+1,'đến',indices[-1]+1,label,solver.status_name(status),point[index])
        if ctx.report(incumbent)['score']<ctx.report(selected)['score']:selected=incumbent
        attempts.append(dict(method='CP_WINDOW',months=[j+1 for j in indices],layers=layers,seconds=round(perf_counter()-begin,3)))
    return selected,attempts
