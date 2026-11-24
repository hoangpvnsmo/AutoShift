"""E05 v4.9: short transitions, same-type streaks, rolling concentration, fairness.
Only SHIFT; streak/rolling diversity only for staff with >=2 allowed types.
A 7-day window is scored at its current-month end if all 7 days are known.
"""
from dataclasses import dataclass
from datetime import timedelta
from core.objectives.rotation_checks import preferred_rest_minutes
from core.validation.transition_checks import transition_config, gap_minutes
from core.objectives.rest_constraints import lex_objective
from core.objectives.rotation_constraints import RotationModel

WINDOW=7
MAX_SAME_WINDOW=3
ONE=timedelta(days=1)

def known_windows(data,days,i):
    current=set(days)
    for d in days:
        w=tuple(d-timedelta(days=k) for k in reversed(range(WINDOW)))
        if all(x in current or (i,x) in data.previous for x in w): yield w

@dataclass(frozen=True)
class FlexRow:
    id:int
    changes:int
    short:int
    adjacent:int
    streak:int
    concentration:int


def flexible_rows(data,days,a):
    days=tuple(days); current=set(days); cfg=transition_config(data); threshold=preferred_rest_minutes(data)
    rows=[]
    for e in data.employees:
        if e.group!='SHIFT': continue
        i=e.id
        def code(d):
            v=a[i,d] if d in current else data.previous.get((i,d))
            return int(v) if str(v) in ('1','2','3') else 0
        changes=short=adjacent=streak=concentration=0
        for d in days:
            p,c=code(d-ONE),code(d)
            if p and c:
                adjacent+=1
                if p!=c:
                    changes+=1; short+=int(gap_minutes(cfg,p,c)<threshold)
            if len(set(e.allowed_shifts))>1 and c and c==p==code(d-2*ONE): streak+=1
        if len(set(e.allowed_shifts))>1:
            for w in known_windows(data,days,i):
                concentration+=sum(max(0,sum(code(d)==s for d in w)-MAX_SAME_WINDOW) for s in (1,2,3))
        rows.append(FlexRow(i,changes,short,adjacent,streak,concentration))
    return tuple(rows)


def flexible_score(rows):
    rows=tuple(rows)
    return (sum(x.short for x in rows),sum(x.streak for x in rows),sum(x.concentration for x in rows),max((x.short+x.streak+x.concentration for x in rows),default=0))


def flexible_targets(data,days,a):
    days=tuple(days); current=set(days); cfg=transition_config(data); threshold=preferred_rest_minutes(data); targets=[]
    for e in data.employees:
        if e.group!='SHIFT': continue
        i=e.id
        def code(d):
            v=a[i,d] if d in current else data.previous.get((i,d))
            return int(v) if str(v) in ('1','2','3') else 0
        for d in days:
            p,c=code(d-ONE),code(d)
            weight=0
            if p and c and p!=c and gap_minutes(cfg,p,c)<threshold: weight+=4
            if len(set(e.allowed_shifts))>1 and c and c==p==code(d-2*ONE): weight+=2
            if weight: targets.append((i,d,weight))
        if len(set(e.allowed_shifts))>1:
            for w in known_windows(data,days,i):
                for s in (1,2,3):
                    excess=max(0,sum(code(d)==s for d in w)-MAX_SAME_WINDOW)
                    if excess: targets.extend((i,d,excess) for d in w if d in current and code(d)==s)
    return targets


def add_flexible_rotation(ctx,data):
    m=ctx.model; days=tuple(ctx.days); current=set(days); D=len(days)
    cfg=transition_config(data); threshold=preferred_rest_minutes(data)
    people=[e for e in data.employees if e.group=='SHIFT']; n=len(people)
    def total(items,cap,name):
        v=m.new_int_var(0,cap,name); m.add(v==sum(items)); return v
    def both(items,name):
        z=m.new_bool_var(name)
        for v in items: m.add(z<=v)
        m.add(z>=sum(items)-len(items)+1); return z
    short=[]; streak=[]; concentration=[]; burden=[]
    for e in people:
        i=e.id; sh=[]; st=[]; co=[]
        def x(d,s):
            return ctx.x[i,d,s] if d in current else int(str(data.previous.get((i,d)))==str(s))
        for d in days:
            for s in ctx.shifts:
                for t in ctx.shifts:
                    if s!=t and gap_minutes(cfg,s,t)<threshold:
                        sh.append(both([x(d-ONE,s),x(d,t)],f'flex_short_{i}_{d}_{s}_{t}'))
                if len(set(e.allowed_shifts))>1:
                    st.append(both([x(d-2*ONE,s),x(d-ONE,s),x(d,s)],f'flex_streak_{i}_{d}_{s}'))
        if len(set(e.allowed_shifts))>1:
            for w in known_windows(data,days,i):
                for s in ctx.shifts:
                    v=m.new_int_var(0,WINDOW-MAX_SAME_WINDOW,f'flex_window_{i}_{w[-1]}_{s}')
                    m.add_max_equality(v,[0,sum(x(d,s) for d in w)-MAX_SAME_WINDOW]); co.append(v)
        a=total(sh,D,f'flex_short_total_{i}'); b=total(st,D,f'flex_streak_total_{i}'); c=total(co,4*D,f'flex_concentration_total_{i}')
        short.append(a); streak.append(b); concentration.append(c); burden.append(total([a,b,c],6*D,f'flex_burden_{i}'))
    worst=m.new_int_var(0,6*D,'flex_max_burden')
    if burden: m.add_max_equality(worst,burden)
    else: m.add(worst==0)
    bounds=(n*D,n*D,4*n*D,6*D)
    terms=(total(short,bounds[0],'flex_short'),total(streak,bounds[1],'flex_streak'),total(concentration,bounds[2],'flex_concentration'),worst)
    return RotationModel(terms,lex_objective(terms,bounds),bounds)
