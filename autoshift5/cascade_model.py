"""Linked month CP-SAT model. Existing monthly business builders stay intact.

Only history-dependent constraints/objectives need linked expressions instead
of constants. The original window definitions and independent scorers are used.
No database or HTTP access occurs here.
"""
from dataclasses import dataclass, replace
from datetime import timedelta
from collections import defaultdict
from ortools.sat.python import cp_model
from core.data.calendar_utils import month_days
from core.model.model_builder import ScheduleModel
from core.constraints.hard_constraints import add_staffing_constraints, add_shift_eligibility
from core.constraints.credit_constraints import add_demand_constraints
from core.constraints.admin_constraints import add_admin_constraints, add_all_request_constraints, add_workforce_objective
from core.validation.sequence_checks import sequence_limits
from core.validation.transition_checks import transition_config, forbidden_pairs, gap_minutes
from core.validation.credit_checks import request_map, target_credit
from core.objectives.soft_constraints import add_shift_balance
from core.objectives.night_constraints import add_night_balance
from core.objectives.day_balance_checks import day_sets
from core.objectives.day_balance_v2 import add_day_balance_v2
from core.objectives.rest_checks import rest_windows
from core.objectives.flexible_rotation import known_windows
from core.objectives.rotation_checks import preferred_rest_minutes
from core.objectives.preference_constraints import add_preferences
from core.engine.engine_config import STAGES
from .domain import CODES, CODE_ID, assert_grid, history_length, Unsupported
from .repair import full_code_variables, indicator_difference
from .validation import frozen_cell, shift_start, short_events, cell_weight

ONE=timedelta(days=1)


@dataclass
class CascadeFrame:
    month_id: int
    data: object
    prefs: tuple
    baseline: dict
    version_id: str | None
    status: str = 'PLANNED'
    locks: tuple = ()
    annual: dict | None = None
    work_policy: dict | None = None
    fixed_data: object = None
    fixed_prefs: tuple = ()
    fixed_policy: dict | None = None
    snapshot_id: str | None = None


def previous_with(data,codes):
    prev=dict(data.previous)
    first=month_days(data.year,data.month)[0]
    for e in data.employees:
        for k in range(1,history_length(data)+1):
            key=e.id,first-k*ONE
            if key in codes:prev[key]=codes[key]
    return replace(data,previous=prev)


class LinkedModel:
    def __init__(self,frames,right,options,*,root_fresh=False,direct_ids=(),quality=True,repair_terms=True):
        self.frames,self.right,self.options=frames,right,options
        self.root_fresh=root_fresh
        self.model=cp_model.CpModel()
        self.contexts=[];self.credits=[];self.values={};self.x={};self.worked={};self._eq={}
        all_baseline={k:c for f in frames for k,c in f.baseline.items()}
        self.data=[previous_with(f.data,all_baseline) for f in frames]
        hours=[transition_config(d).intervals for d in self.data]
        if right:hours.append(transition_config(right.fixed_data or right.data).intervals)
        if any(h!=hours[0] for h in hours[1:]):
            raise Unsupported('Sửa cuốn chiếu chưa hỗ trợ đổi giờ bắt đầu/kết thúc loại ca giữa các tháng.')
        for frame,data in zip(frames,self.data):
            if frame.baseline:assert_grid(data,frame.baseline)
            days=month_days(data.year,data.month)
            if data.rules['max_shifts_per_day']!=1:raise ValueError('Chỉ hỗ trợ một ca/ngày.')
            ids=tuple(e.id for e in data.employees)
            x={(i,d,s):self.model.new_bool_var(f'linked_x_{i}_{d}_{s}') for i in ids for d in days for s in (1,2,3)}
            worked={(i,d):self.model.new_bool_var(f'linked_work_{i}_{d}') for i in ids for d in days}
            for (i,d),w in worked.items():self.model.add(w==sum(x[i,d,s] for s in (1,2,3)))
            ctx=ScheduleModel(self.model,days,ids,(1,2,3),x,worked)
            self.contexts.append(ctx);self.x.update(x);self.worked.update(worked)
            self.values.update(full_code_variables(ctx,data))
        for frame,data,ctx in zip(frames,self.data,self.contexts):
            add_staffing_constraints(ctx,data);add_shift_eligibility(ctx,data)
            demand=add_demand_constraints(ctx,data)
            add_admin_constraints(ctx,data);add_all_request_constraints(ctx,data)
            credit=add_workforce_objective(ctx,data);self.credits.append(credit)
            shift_ids={e.id for e in data.employees if e.group=='SHIFT'}
            fixed=sum(c in ('CT','DT') for (i,d),c in request_map(data).items() if i in shift_ids)
            capacity=sum(target_credit(data,e) for e in data.employees if e.id in shift_ids)
            self.model.add(credit.shift_excess+credit.admin_total>=sum(demand.values())+fixed-capacity)
            self._sequences(data,ctx.days)
            for key,var in ((k,self.values[k]) for k in ctx.worked):
                if key in frame.baseline and (frame.status=='ACTUAL' or frozen_cell(data,key,frame.baseline,options.as_of,
                        set(frame.locks),set(ctx.days) if frame is not frames[0] else self.root_allowed(ctx.days))):
                    self.model.add(var==CODE_ID[frame.baseline[key]])
                for s in (1,2,3):
                    if frame.baseline.get(key)!=str(s) and shift_start(data,key[1],str(s))<=options.as_of:
                        self.model.add(ctx.x[key[0],key[1],s]==0)
        if right:
            rd=right.fixed_data or right.data
            n=max(*sequence_limits(rd),transition_config(rd).lookback,1)
            self._sequences(rd,month_days(rd.year,rd.month)[:n])
        self.model.clear_objective()
        self.qualities=[];self.active=[]
        if quality:
            for data,ctx,credit,frame in zip(self.data,self.contexts,self.credits,frames):
                terms,active=self._quality(ctx,data,credit,frame.prefs)
                self.qualities.append(terms);self.active.append(active)
        if repair_terms:self._repair_terms(direct_ids)

    def root_allowed(self,days):
        return {d for d in days if (self.options.allowed_from is None or d>=self.options.allowed_from)
                and (self.options.allowed_until is None or d<=self.options.allowed_until)}

    def code(self,i,d,owner):
        if (i,d) in self.values:return self.values[i,d]
        if self.right and (i,d) in self.right.baseline:return CODE_ID[self.right.baseline[i,d]]
        c=owner.previous.get((i,d))
        if c not in CODES:raise ValueError(f'Thiếu mã lịch sử liên kết: ID {i}, {d}; không suy diễn OFF.')
        return CODE_ID[c]

    def eq(self,i,d,c,owner):
        if (i,d) in self.values:
            if c in ('1','2','3'):return self.x[i,d,int(c)]
            key=i,d,c
            if key not in self._eq:
                self._eq[key]=1-indicator_difference(self.model,self.values[i,d],CODE_ID[c],f'linked_eq_{i}_{d}_{c}')
            return self._eq[key]
        return int(self.code(i,d,owner)==CODE_ID[c])

    def work(self,i,d,owner):
        if (i,d) in self.worked:return self.worked[i,d]
        return int(self.code(i,d,owner) in (CODE_ID['1'],CODE_ID['2'],CODE_ID['3']))

    def both(self,items,name):
        b=self.model.new_bool_var(name)
        for v in items:self.model.add(b<=v)
        self.model.add(b>=sum(items)-len(items)+1)
        return b

    def total(self,items,cap,name):
        v=self.model.new_int_var(0,cap,name);self.model.add(v==sum(items));return v

    def maximum(self,items,cap,name):
        v=self.model.new_int_var(0,cap,name)
        if items:self.model.add_max_equality(v,items)
        else:self.model.add(v==0)
        return v

    def gap(self,items,cap,name):
        if not items:return 0
        hi=self.maximum(items,cap,name+'_hi');lo=self.model.new_int_var(0,cap,name+'_lo')
        self.model.add_min_equality(lo,items)
        return hi-lo

    def _sequences(self,data,days):
        m=self.model;night,work=sequence_limits(data);cfg=transition_config(data)
        pairs={k:forbidden_pairs(cfg,k) for k in range(1,cfg.lookback+1)}
        for d in days:
            for s in (1,2,3):
                carry=[self.both([self.eq(e.id,d,str(s),data),self.work(e.id,d-ONE,data)],f'linked_carry_{e.id}_{d}_{s}') for e in data.employees]
                m.add(sum(carry)>=data.rules['min_previous_day_staff'])
            for e in data.employees:
                i=e.id
                m.add(sum(self.eq(i,d-k*ONE,'1',data) for k in range(night+1))<=night)
                m.add(sum(self.work(i,d-k*ONE,data) for k in range(work+1))<=work)
                for k,forbidden in pairs.items():
                    for a,b in forbidden:m.add(self.eq(i,d-k*ONE,str(a),data)+self.eq(i,d,str(b),data)<=1)

    def _quality(self,ctx,data,credit,prefs):
        balance=add_shift_balance(ctx,data);night=add_night_balance(ctx,data,balance.counts)
        holiday,weekend=day_sets(data,ctx.days)
        _,ht,_=add_day_balance_v2(ctx,data,holiday,'holiday');_,wt,_=add_day_balance_v2(ctx,data,weekend,'weekend')
        rest_a,rest_b=self._rest(ctx,data)
        rotation=self._rotation(ctx,data)
        pt,_,_=add_preferences(ctx,data,prefs)
        terms=[(credit.shift_excess,credit.admin_total,credit.shift_spread,credit.shift_deviation,credit.admin_deviation),
            (balance.worst_gap,balance.total_gap),(night.worst_gap,night.total_gap),ht,wt,rest_a,rest_b,rotation,pt]
        active=[spec.flag is None or data.rules.get('optimization',{}).get(spec.flag,True) for spec in STAGES]
        return terms,active

    def _rest(self,ctx,data):
        w=rest_windows(data,ctx.days);D=len(ctx.days);ids=[e.id for e in data.employees if e.group=='SHIFT'];n=len(ids);W=len(w.weekends)
        singles=[];extras=[];blocks=[];weekends=[];zeros=[]
        for i in ids:
            off=lambda d:self.eq(i,d,'OFF',data)
            singles.append(self.total([self.both([1-off(a),off(b),1-off(c)],f'linked_single_{i}_{b}') for a,b,c in w.singles],D,f'linked_singles_{i}_{ctx.days[0]}'))
            extras.append(self.total([self.both([off(d) for d in ds],f'linked_long_{i}_{ds[-1]}') for ds in w.long_windows],D,f'linked_extras_{i}_{ctx.days[0]}'))
            blocks.append(self.total([self.both([1-off(a),off(b),off(c)],f'linked_block_{i}_{c}') for a,b,c in w.blocks],D,f'linked_blocks_{i}_{ctx.days[0]}'))
            v=self.total([self.both([off(sat),off(sun)]+[1-self.eq(i,d,str(s),data) for d,s in pairs],f'linked_weekend_{i}_{sun}') for sat,sun,pairs in w.weekends],W,f'linked_weekends_{i}_{ctx.days[0]}')
            weekends.append(v)
            if W:
                z=self.model.new_bool_var(f'linked_zero_weekend_{i}_{ctx.days[0]}');self.model.add(v==0).only_enforce_if(z);self.model.add(v>=1).only_enforce_if(~z);zeros.append(z)
        return (sum(singles),self.maximum(singles,D,f'linked_worst_single_{ctx.days[0]}'),sum(extras)),(
            sum(zeros),n*W-sum(weekends),self.gap(weekends,W,f'linked_weekend_gap_{ctx.days[0]}'),self.gap(blocks,D,f'linked_block_gap_{ctx.days[0]}'))

    def _rotation(self,ctx,data):
        cfg=transition_config(data);threshold=preferred_rest_minutes(data);D=len(ctx.days)
        sh=[];st=[];co=[];burden=[]
        for e in data.employees:
            if e.group!='SHIFT':continue
            i=e.id;a=[];b=[];c=[]
            for d in ctx.days:
                for s in (1,2,3):
                    for t in (1,2,3):
                        if s!=t and gap_minutes(cfg,s,t)<threshold:
                            a.append(self.both([self.eq(i,d-ONE,str(s),data),self.eq(i,d,str(t),data)],f'linked_short_{i}_{d}_{s}_{t}'))
                    if len(set(e.allowed_shifts))>1:
                        b.append(self.both([self.eq(i,d-k*ONE,str(s),data) for k in (2,1,0)],f'linked_streak_{i}_{d}_{s}'))
            if len(set(e.allowed_shifts))>1:
                for window in known_windows(data,ctx.days,i):
                    for s in (1,2,3):
                        v=self.model.new_int_var(0,4,f'linked_concentration_{i}_{window[-1]}_{s}')
                        self.model.add_max_equality(v,[0,sum(self.eq(i,d,str(s),data) for d in window)-3]);c.append(v)
            av=self.total(a,D,f'linked_short_total_{i}_{ctx.days[0]}');bv=self.total(b,D,f'linked_streak_total_{i}_{ctx.days[0]}');cv=self.total(c,4*D,f'linked_concentration_total_{i}_{ctx.days[0]}')
            sh.append(av);st.append(bv);co.append(cv);burden.append(av+bv+cv)
        return sum(sh),sum(st),sum(co),self.maximum(burden,6*D,f'linked_worst_rotation_{ctx.days[0]}')

    def _repair_terms(self,direct_ids):
        m=self.model;self.penalized=list(range(1 if self.root_fresh else 0,len(self.frames)))
        self.z={};affected=defaultdict(list);months=[];near=[];extra=[];admin=[];short=[];workdiff=[];annual=[]
        old_flat={**{k:c for f in reversed(self.frames) for k,c in f.data.previous.items()},**{k:c for f in self.frames for k,c in f.baseline.items()}}
        for j in self.penalized:
            f,data,ctx,credit=self.frames[j],self.data[j],self.contexts[j],self.credits[j]
            assert_grid(data,f.baseline);D=len(ctx.days);N=len(ctx.employee_ids)
            z={k:indicator_difference(m,self.values[k],CODE_ID[f.baseline[k]],f'linked_changed_{k[0]}_{k[1]}') for k in ctx.worked};self.z.update(z)
            mm=self.maximum(list(z.values()),1,f'linked_month_changed_{f.month_id}');months.append(mm)
            for (i,d),v in z.items():
                if i not in direct_ids:affected[i].append(v)
                near.append(cell_weight(d,self.options.as_of,f.status=='PUBLISHED')*v)
            totals={e.id:sum(data.rules['work_credit'][f.baseline[e.id,d]] for d in ctx.days) for e in data.employees}
            before_excess=sum(max(0,totals[e.id]-target_credit(data,e)) for e in data.employees)
            admin_ids={e.id for e in data.employees if e.group=='ADMIN'}
            before_admin=sum(c in ('1','2','3') for (i,d),c in f.baseline.items() if i in admin_ids)
            extra.append(self.maximum([sum(credit.excess.values())-before_excess,0],N*D,f'linked_extra_{f.month_id}'))
            admin.append(self.maximum([credit.admin_total-before_admin,0],N*D,f'linked_admin_{f.month_id}'))
            if self.options.no_admin_increase:m.add(credit.admin_total<=before_admin)
            if self.options.no_overtime_increase:m.add(sum(credit.excess.values())<=before_excess)
            for e in data.employees:
                v=m.new_int_var(0,D,f'linked_work_diff_{e.id}_{f.month_id}');m.add_abs_equality(v,credit.total[e.id]-totals[e.id]);workdiff.append(v)
            old_short=short_events(data,old_flat);cfg=transition_config(data);threshold=preferred_rest_minutes(data)
            for e in data.employees:
                if e.group!='SHIFT':continue
                for d in ctx.days:
                    if (e.id,d) in old_short:continue
                    for a in (1,2,3):
                        for b in (1,2,3):
                            if a!=b and gap_minutes(cfg,a,b)<threshold:
                                short.append(self.both([self.eq(e.id,d-ONE,str(a),data),self.eq(e.id,d,str(b),data)],f'linked_new_short_{e.id}_{d}_{a}_{b}'))
            if f.annual:
                assert_grid(data,f.annual)
                annual.extend(indicator_difference(m,self.values[k],CODE_ID[f.annual[k]],f'linked_v0_{k[0]}_{k[1]}') for k in ctx.worked)
        other=[self.maximum(v,1,f'linked_affected_{i}') for i,v in affected.items()]
        self.groups=[(sum(self.z.values()),),(sum(other),),(sum(near),),(sum(extra),sum(admin)),(sum(short),),(sum(workdiff),),(sum(annual),)]
        self.months_changed=sum(months)
        if self.options.max_changes is not None:m.add(self.groups[0][0]<=self.options.max_changes)
        self.terms=[('R1',self.groups[0][0]),('MONTHS_CHANGED',self.months_changed)]
        self.terms.extend((f'R{j+1}',t) for j,terms in enumerate(self.groups[1:],1) if f'R{j+1}' not in self.options.disabled_layers for t in terms)
        if 'R8' not in self.options.disabled_layers:
            for tier in range(9):
                for j,terms in enumerate(self.qualities):
                    if self.active[j][tier]:self.terms.extend((f'R8.{tier}.{j}',t) for t in terms[tier])
