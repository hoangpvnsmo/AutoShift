"""Compact immutable workload ledger captured with a repair's input revision."""
from collections import Counter
from fractions import Fraction
from sqlalchemy import select
from core.data.calendar_utils import month_days,standard_credit
from core.validation.credit_checks import request_map,target_credit
from . import models as m
from .repository import load_month,codes_for


def month_record(data,codes,month_id=None,version_id=None):
    days=month_days(data.year,data.month);requests=request_map(data);standard=standard_credit(data)
    people=[]
    for e in data.employees:
        if e.group!='SHIFT':continue
        counts=Counter(codes[e.id,d] for d in days)
        eligible=sum(requests.get((e.id,d)) not in ('OFF','CT','DT','BKD') for d in days)
        weight=round(1000*eligible*(Fraction(target_credit(data,e),standard) if standard else 1))
        people.append(dict(id=e.id,name=e.name,shifts=sum(counts[c] for c in ('1','2','3')),night=counts['1'],
            weight=weight,night_weight=weight if 1 in e.allowed_shifts else 0,night_eligible=1 in e.allowed_shifts))
    return dict(year=data.year,month=data.month,month_id=month_id,version_id=version_id,days=len(days),employees=people)


def capture_context(s,months):
    years={month.year for month in months};records=[]
    for month in s.scalars(select(m.PlanningMonth).where(m.PlanningMonth.year.in_(years)).order_by(m.PlanningMonth.year,m.PlanningMonth.month)):
        if not month.current_version_id:continue
        data,_=load_month(s,month.id)
        records.append(month_record(data,codes_for(s,month.current_version_id),month.id,month.current_version_id))
    return dict(months=records,policy='Known confirmed months only; missing months are not OFF.')


def right_frame(s,data):
    from datetime import timedelta
    from .domain import deserialize
    from .cascade_model import CascadeFrame
    day=month_days(data.year,data.month)[-1]+timedelta(days=1)
    month=s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==day.year,m.PlanningMonth.month==day.month))
    if not month or not month.current_version_id:return None
    v=s.get(m.ScheduleVersion,month.current_version_id);snap=s.get(m.InputSnapshot,v.snapshot_id);fd,fp=deserialize(snap.payload)
    return CascadeFrame(month.id,fd,fp,codes_for(s,v.id),v.id,status=v.status,fixed_data=fd,fixed_prefs=fp,fixed_policy=snap.work_policy)
