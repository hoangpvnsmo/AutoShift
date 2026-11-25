"""Warm-started annual rebalancing with cheap exchanges and bounded CP windows."""
from dataclasses import replace
from datetime import timedelta
from random import Random
from time import perf_counter
from core.data.calendar_utils import month_days
from core.validation.credit_checks import check_staffing, target_credit
from core.validation.sequence_checks import check_sequences
from core.validation.transition_checks import check_transitions
from .cascade import CascadeResult, inspect_candidate
from .cascade_model import previous_with
from .domain import numeric
from .validation import frozen_cell, shift_start, validate_month
from .year_metrics import YearMetrics, SHIFT, ONE


class ExchangeSearch:
    def __init__(self,context,options):
        self.context=context;self.options=options;self.codes=dict(context.original)
        self.rows,self.monthly,self.full,self.resource=context.counts(self.codes)
        self.changed=0;self.key=context.score(self.rows,self.monthly,self.resource)
        self.initial_resource=self.resource;self.modifiable=set();self.attempts=0;self.accepted=0
        for j,f in enumerate(context.frames):
            days=month_days(f.data.year,f.data.month)
            allowed={d for d in days if (options.allowed_from is None or d>=options.allowed_from)
                and (options.allowed_until is None or d<=options.allowed_until)}
            if f.status in ('ACTUAL','PUBLISHED'):continue
            for e in f.data.employees:
                if e.group!='SHIFT':continue
                for d in days:
                    k=e.id,d
                    if k not in context.requests and not frozen_cell(f.data,k,f.baseline,options.as_of,set(f.locks),allowed):self.modifiable.add(k)
        if options.max_changes==0:self.modifiable.clear()

    def exchange(self,i,j,days):
        result={}
        for day in days:
            a,b=(i,day),(j,day)
            if a not in self.codes or b not in self.codes:return {}
            ca,cb=self.codes[a],self.codes[b]
            if ca==cb:continue
            if a not in self.modifiable or b not in self.modifiable:return {}
            if ca not in SHIFT|{'OFF'} or cb not in SHIFT|{'OFF'}:return {}
            frame=self.context.frames[self.context.day_frame[day]]
            for k,c in ((a,cb),(b,ca)):
                person=self.context.person[k[0],self.context.day_frame[day]]
                if (c in SHIFT and (int(c) not in person.allowed_shifts or shift_start(frame.data,day,c)<=self.options.as_of)):
                    return {}
                if k in self.context.requests and c!=self.context.requests[k]:return {}
            result[a],result[b]=cb,ca
        return result

    def evaluate(self,changes):
        ctx=self.context;rows={i:dict(row) for i,row in self.rows.items()};monthly=dict(self.monthly)
        full=dict();affected_months=set();events=set();changed=self.changed
        for (i,day),code in changes.items():
            before=self.codes[i,day];frame=ctx.day_frame[day];data=ctx.frames[frame].data
            delta=int(code in SHIFT)-int(before in SHIFT)
            rows[i]['total_shifts']+=delta
            if day.weekday()>=5:rows[i]['weekend_shifts']+=delta
            credit=data.rules['work_credit'][code]-data.rules['work_credit'][before]
            rows[i]['work']+=credit;monthly[i,frame]+=credit;affected_months.add((i,frame))
            events.update(ctx.events_for[i,day])
            original=ctx.original[i,day]
            changed+=int(code!=original)-int(before!=original)
        if self.options.max_changes is not None and changed>self.options.max_changes:return None
        excess=self.resource[0]
        for i,j in affected_months:
            target=target_credit(ctx.frames[j].data,ctx.person[i,j])
            excess+=max(0,monthly[i,j]-target)-max(0,self.monthly[i,j]-target)
        resource=excess,self.resource[1]
        if any(a>b for a,b in zip(resource,self.initial_resource)):return None
        for n in events:
            value=ctx.full(n,self.codes,changes);full[n]=value
            rows[ctx.events[n][0]]['full_weekends']+=value-self.full[n]
        if sum(row['full_weekends'] for row in rows.values())<ctx.full_floor:return None
        key=ctx.score(rows,monthly,resource,changed)
        if key>=self.key:return None
        return key,rows,monthly,full,resource,changed

    def valid(self,changes):
        """Use original independent hard checkers only on affected months/people."""
        ctx=self.context;proposed={**self.codes,**changes}
        month_ids={ctx.day_frame[d] for i,d in changes}
        month_ids|={j+1 for j in list(month_ids) if j+1<len(ctx.frames)}
        ids={i for i,d in changes}
        for j in sorted(month_ids):
            f=ctx.frames[j];data=previous_with(f.data,proposed);days=month_days(data.year,data.month)
            assignment={(e.id,d):int(proposed[e.id,d]) if proposed[e.id,d] in SHIFT else 0 for e in data.employees for d in days}
            if check_staffing(data,days,assignment):return False
            part=replace(data,employees=tuple(e for e in data.employees if e.id in ids))
            if check_sequences(part,days,assignment)[0] or check_transitions(part,days,assignment)[0]:return False
            if f.work_policy:
                try:validate_month(data,{k:proposed[k] for k in assignment},f.prefs,f.work_policy)
                except ValueError:return False
        if ctx.frontier and len(ctx.frames)-1 in month_ids:
            right=ctx.frontier;data=previous_with(right.fixed_data or right.data,proposed)
            try:validate_month(data,right.baseline,right.fixed_prefs,right.fixed_policy)
            except ValueError:return False
        return True

    def try_exchange(self,i,j,days):
        self.attempts+=1
        changes=self.exchange(i,j,days)
        if not changes:return False
        evaluation=self.evaluate(changes)
        if evaluation is None or not self.valid(changes):return False
        self.key,self.rows,self.monthly,full,self.resource,self.changed=evaluation
        for n,value in full.items():self.full[n]=value
        self.codes.update(changes);self.accepted+=1
        return True


def balance_year(frames,frontier,options,*,window_months=3,emit=lambda *a:None,cancelled=lambda:False):
    start=perf_counter();deadline=start+options.budget
    if not frames or any(not f.baseline for f in frames):raise ValueError('Cân năm cần lịch tham chiếu đầy đủ của mọi tháng.')
    # Whole-year counting must include the frozen history and the unmodified tail.
    prepared=[]
    for f in frames:
        days=month_days(f.data.year,f.data.month)
        extra={k for k in f.baseline if (options.allowed_from and k[1]<options.allowed_from)
            or (options.allowed_until and k[1]>options.allowed_until) or f.status in ('ACTUAL','PUBLISHED')}
        prepared.append(replace(f,locks=tuple(set(f.locks)|extra)))
    frames=prepared;ctx=YearMetrics(frames,frontier);original=dict(ctx.original)
    before=ctx.report(original)
    inspect_candidate(frames,frontier,original,options)
    search=ExchangeSearch(ctx,options);rng=Random(2027);ids=ctx.ids;days=ctx.days
    emit('Cân năm bắt đầu:',before['ranges'],'| ngân sách',options.budget,'giây')
    if cancelled():return CascadeResult(status='CANCELLED')
    # Single-day transfers rapidly repair gross shift-count imbalances.
    def at_equal_target(name):
        weights=ctx.weights[name]
        values=[row[name] for row in search.rows.values()]
        return bool(weights) and len(set(weights.values()))==1 and min(weights.values())>0 and max(values)-min(values)<=1
    end=min(deadline,start+options.budget*.22);last_emit=start
    for sweep in range(4 if search.modifiable else 0):
        if at_equal_target('total_shifts'):break
        order=list(days);rng.shuffle(order);improved=False
        pairs=[(i,j) for i in ids for j in ids if i!=j]
        pairs.sort(key=lambda pair:search.rows[pair[1]]['total_shifts']-search.rows[pair[0]]['total_shifts'])
        for i,j in pairs:
            if perf_counter()>=end or cancelled():break
            for d in order:
                if search.codes.get((i,d)) not in SHIFT or search.codes.get((j,d))!='OFF':continue
                if search.try_exchange(i,j,[d]):improved=True
                if at_equal_target('total_shifts'):break
                if perf_counter()>=end:break
            if at_equal_target('total_shifts'):break
        emit('Chuyển ca cân tổng năm:',sweep+1,'| nhận',search.accepted,'|',ctx.report(search.codes)['ranges'])
        if not improved or perf_counter()>=end or cancelled():break
    # Pair a weekend transfer with a weekday transfer to keep annual totals.
    end=min(deadline,start+options.budget*.42)
    for attempt in range(18000 if search.modifiable else 0):
        if at_equal_target('weekend_shifts'):break
        if attempt%50==0 and (perf_counter()>=end or cancelled()):break
        if len(ids)<2:break
        i,j=rng.sample(ids,2)
        weekend=[d for d in days if d.weekday()>=5 and search.codes.get((i,d)) in SHIFT and search.codes.get((j,d))=='OFF']
        weekday=[d for d in days if d.weekday()<5 and search.codes.get((j,d)) in SHIFT and search.codes.get((i,d))=='OFF']
        if weekend and weekday:search.try_exchange(i,j,[rng.choice(weekend),rng.choice(weekday)])
    emit('Cân ca cuối tuần:',ctx.report(search.codes)['ranges'],'| số ô đổi',search.changed)
    # Contiguous and paired week blocks redistribute genuine full weekends.
    end=min(deadline,start+options.budget*.70);stalled=0
    for attempt in range(45000 if search.modifiable else 0):
        if attempt%50==0 and (perf_counter()>=end or cancelled()):break
        if len(ids)<2:break
        i,j=rng.sample(ids,2);length=rng.choice((2,3,7,7,14))
        first=rng.randrange(max(1,len(days)-length+1));block=days[first:first+length]
        if attempt%3==0:
            second=rng.randrange(max(1,len(days)-length+1));block=sorted(set(block+days[second:second+length]))
        if search.try_exchange(i,j,block):stalled=0
        else:stalled+=1
        if stalled>=9000:break
    attempts=[dict(method='EXCHANGE',attempts=search.attempts,accepted=search.accepted,seconds=round(perf_counter()-start,3))]
    if cancelled():return CascadeResult(status='CANCELLED',attempts=attempts)
    measured=ctx.report(search.codes)
    if list(search.key)!=measured['score']:raise ValueError('Điểm cân năm tăng dần không khớp tính lại độc lập.')
    # A bounded linked CP window can make coordinated changes unavailable to swaps.
    if search.modifiable and deadline-perf_counter()>5:
        from .year_balance_cp import refine_windows
        codes,cp_attempts=refine_windows(ctx,search.codes,options,deadline=deadline,window_months=window_months,
            emit=emit,cancelled=cancelled)
        attempts.extend(cp_attempts)
    else:codes=search.codes
    if cancelled():return CascadeResult(status='CANCELLED',attempts=attempts)
    entries,boundaries,metrics=inspect_candidate(frames,frontier,codes,options)
    after=ctx.report(codes)
    if after['score']>before['score']:raise ValueError('Cân năm làm xấu vector ưu tiên đã tính độc lập.')
    proof=dict(status='FEASIBLE_ANNUAL_BALANCE',objective='ANNUAL_BALANCE',r1_global_optimal=False,
        quality_proven=False,annual_global_optimal=False,months=[f.month_id for f in frames],
        policy='Nguồn lực → tổng ca năm → ca cuối tuần năm → nghỉ trọn cuối tuần → công tháng → số ô đổi.',
        seconds=round(perf_counter()-start,3))
    metrics['annual_balance']=dict(before=before,after=after)
    emit('Cân năm hoàn tất:',after['ranges'],'|',metrics['changed_cells'],'ô đổi; bản nháp chờ duyệt')
    return CascadeResult(status='FEASIBLE',entries=entries,boundaries=boundaries,metrics=metrics,proof=proof,attempts=attempts)
