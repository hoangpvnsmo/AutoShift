"""Final rolling-horizon fairness tier, after all active monthly quality scores.
Compares only employees with the same group, shift permissions and target credit.
It never participates ahead of REPAIR's stability tiers.
"""
from collections import defaultdict, Counter
from time import perf_counter
from datetime import timedelta
from ortools.sat.python import cp_model
from core.model.engine_model import build_stage3
from core.data.calendar_utils import month_days, standard_credit
from core.validation.credit_checks import request_map, target_credit
from core.validation.admin_checks import check_admin_credits
from core.validation.transition_checks import transition_config
from core.objectives.rotation_checks import preferred_rest_minutes
from core.objectives.rest_checks import rest_windows
from validator.quality import score_vector
from .validation import validate_month, short_events
from .domain import numeric
from .quality_model import quality_terms

METRICS=('shift_1','shift_2','shift_3','holiday','weekend','full_weekends','overtime','short_rest')


def eligibility_record(data,employee):
    unavailable=Counter(r.code for r in data.requests if r.employee_id==employee.id)
    return (data.year,data.month,employee.group,employee.is_leader,employee.allowed_shifts,
            target_credit(data,employee),tuple(sorted(unavailable.items())))


def fairness_group(data,employee,history):
    return eligibility_record(data,employee),history.get(employee.id,{}).get('_eligibility_history',())


def counts_for(data,codes):
    days=month_days(data.year,data.month)
    short=short_events(data,codes)
    w=rest_windows(data,days)
    out={}
    for e in data.employees:
        i=e.id
        c=Counter(codes[i,d] for d in days)
        cell=lambda d: codes.get((i,d),data.previous.get((i,d)))
        full=sum(cell(sat)=='OFF' and cell(sun)=='OFF' and all(cell(d)!=str(k) for d,k in pairs) for sat,sun,pairs in w.weekends) if e.group=='SHIFT' else 0
        work=sum(data.rules['work_credit'][codes[i,d]] for d in days)
        out[i]=dict(shift_1=c['1'],shift_2=c['2'],shift_3=c['3'],
            holiday=sum(codes[i,d] in ('1','2','3') for d in days if d in data.holidays),
            weekend=sum(codes[i,d] in ('1','2','3') for d in days if d.weekday()>=5 and d not in data.holidays),full_weekends=full,
            overtime=max(0,work-target_credit(data,e)),short_rest=sum(eid==i for eid,d in short))
    return out


def fairness_key(data,counts,history):
    groups=defaultdict(list)
    for e in data.employees:groups[fairness_group(data,e,history)].append(e.id)
    gaps=[]
    for ids in groups.values():
        if len(ids)<2:continue
        for name in METRICS:
            vals=[history.get(i,{}).get(name,0)+counts[i][name] for i in ids]
            gaps.append(max(vals)-min(vals))
    return max(gaps,default=0),sum(gaps)


def refine_annual(data,prefs,codes,report,history,*,budget,work_policy=None,emit=lambda *a:None):
    before=fairness_key(data,counts_for(data,codes),history)
    if budget<=.02 or before==(0,0):
        return codes,report,dict(before=before,after=before,status='NOT_RUN' if before!=(0,0) else 'BOUND_ZERO')
    start=perf_counter()
    ctx,_,_,credit=build_stage3(data)
    ctx.model.clear_objective()
    q,active=quality_terms(ctx,data,credit,prefs)
    scores=score_vector(report)
    for terms,on,values in zip(q,active,scores):
        if on:
            for term,value in zip(terms,values):ctx.model.add(term==value)
    m=ctx.model
    req=request_map(data)
    D=len(ctx.days)
    def both(items,name):
        v=m.new_bool_var(name)
        for item in items:m.add(v<=item)
        m.add(v>=sum(items)-len(items)+1)
        return v
    cfg=transition_config(data)
    threshold=preferred_rest_minutes(data)
    w=rest_windows(data,ctx.days)
    rows={}
    for e in data.employees:
        i=e.id
        def x(day,shift):return ctx.x[i,day,shift] if day in ctx.days else int(data.previous.get((i,day))==str(shift))
        def off(day):
            if day not in ctx.days:return int(data.previous.get((i,day))=='OFF')
            if e.group!='SHIFT' or req.get((i,day)) in ('CT','DT','BKD'):return 0
            return 1-ctx.worked[i,day]
        full=[both([off(sat),off(sun)]+[1-x(day,k) for day,k in pairs],f'annual_full_{i}_{sun}') for sat,sun,pairs in w.weekends] if e.group=='SHIFT' else []
        short=[]
        if e.group=='SHIFT':
            for d in ctx.days:
                for a in ctx.shifts:
                    for b in ctx.shifts:
                        if a!=b and 1440+cfg.intervals[b][0]-cfg.intervals[a][1]<threshold:
                            short.append(both([x(d-timedelta(days=1),a),x(d,b)],f'annual_short_{i}_{d}_{a}_{b}'))
        rows[i]={**{f'shift_{k}':sum(ctx.x[i,d,k] for d in ctx.days) for k in ctx.shifts},
            'holiday':sum(ctx.worked[i,d] for d in ctx.days if d in data.holidays),
            'weekend':sum(ctx.worked[i,d] for d in ctx.days if d.weekday()>=5 and d not in data.holidays),
            'full_weekends':sum(full),'overtime':credit.excess[i],'short_rest':sum(short)}
    groups=defaultdict(list)
    for e in data.employees:groups[fairness_group(data,e,history)].append(e.id)
    gaps=[]
    cap=max((row.get(name,0) for row in history.values() for name in METRICS),default=0)+D
    for ids in groups.values():
        if len(ids)<2:continue
        for name in METRICS:
            values=[]
            for i in ids:
                v=m.new_int_var(0,cap,f'annual_cumulative_{i}_{name}')
                m.add(v==rows[i][name]+history.get(i,{}).get(name,0));values.append(v)
            hi=m.new_int_var(0,cap,f'annual_high_{len(gaps)}');lo=m.new_int_var(0,cap,f'annual_low_{len(gaps)}')
            m.add_max_equality(hi,values);m.add_min_equality(lo,values)
            gap=m.new_int_var(0,cap,f'annual_gap_{len(gaps)}');m.add(gap==hi-lo);gaps.append(gap)
    worst=m.new_int_var(0,cap,'annual_worst_gap')
    if gaps:m.add_max_equality(worst,gaps)
    else:m.add(worst==0)
    terms=(worst,sum(gaps))
    selected,selected_report=codes,report
    statuses=[]
    for index,term in enumerate(terms):
        remain=budget-(perf_counter()-start)
        if remain<=.01:break
        m.minimize(term)
        m.clear_hints()
        for (i,d,k),v in ctx.x.items():m.add_hint(v,int(selected[i,d]==str(k)))
        m.add(term<=fairness_key(data,counts_for(data,selected),history)[index])
        solver=cp_model.CpSolver();solver.parameters.num_search_workers=8
        solver.parameters.max_time_in_seconds=max(.01,remain/(len(terms)-index))
        status=solver.solve(m)
        if status not in (cp_model.OPTIMAL,cp_model.FEASIBLE):break
        a={(i,d):next((k for k in ctx.shifts if solver.value(ctx.x[i,d,k])),0) for i,d in ctx.worked}
        errors,proposed,_=check_admin_credits(data,ctx.days,a)
        proposed_report=validate_month(data,proposed,prefs,work_policy)
        after=fairness_key(data,counts_for(data,proposed),history)
        actual=score_vector(proposed_report)
        if errors or any(on and left!=right for left,right,on in zip(scores,actual,active)) or after[index]!=int(solver.value(term)):
            raise ValueError('Tầng cân bằng năm không khớp kiểm tra độc lập hoặc làm xấu mục tiêu tháng.')
        selected,selected_report=proposed,proposed_report
        m.add(term==after[index]);statuses.append(solver.status_name(status))
    after=fairness_key(data,counts_for(data,selected),history)
    if after>before:raise ValueError('Tầng tích lũy năm làm xấu điểm đã đạt.')
    emit('Cân bằng tích lũy năm:',before,'->',after,'|',statuses)
    return selected,selected_report,dict(before=before,after=after,statuses=statuses,
        policy='Sau toàn bộ tầng tháng đang bật; nhóm cùng quyền ca, vai trò, công chuẩn.')
