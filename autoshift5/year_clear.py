"""Reset a future draft year without deleting immutable schedule history."""
from collections import Counter
from datetime import date,timedelta
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select,func,update,delete,or_
from . import models as m
from .db import ROOT,revision,bump,audit,backup
from .domain import Conflict,now,LOCAL_TZ
from .repository import require,codes_for,load_month
from .validation import shift_start


def cleared_history(months):
    """Only derived days being rebuilt; retain a partial plan's initial anchor."""
    return or_(False,*(m.HistoricalAssignment.day.between(date(x.year,x.month,1),
        date(x.year+int(x.month==12),x.month%12+1,1)-timedelta(days=1)) for x in months))


def inspect_clear(s,plan_id):
    plan=require(s,m.AnnualPlan,plan_id);year=plan.year
    months=list(s.scalars(select(m.PlanningMonth).where(m.PlanningMonth.year==year).order_by(m.PlanningMonth.month)))
    mids={month.id for month in months}
    plans=list(s.scalars(select(m.AnnualPlan).where(m.AnnualPlan.year==year)))
    pids={p.id for p in plans}
    versions=list(s.scalars(select(m.ScheduleVersion).where(m.ScheduleVersion.month_id.in_(mids))))
    vids={v.id for v in versions}
    previous=s.scalar(select(m.AuditLog).where(m.AuditLog.action=='CLEAR_YEAR',m.AuditLog.entity_id==str(year)).order_by(m.AuditLog.id.desc()).limit(1))
    archived=set(previous.payload.get('archived_version_ids',[])) if previous else set()
    jobs=[]
    for job in s.scalars(select(m.SolverRun).where(m.SolverRun.status=='SUCCEEDED')):
        owns=job.annual_plan_id in pids or job.month_id in mids
        result=job.result or {}
        touches=not result.get('confirmed') and bool(set(result.get('version_ids',[]))&vids)
        if owns or touches:jobs.append(job)
    first,last=date(year,1,1),date(year+1,1,1)-timedelta(days=1)
    requests=list(s.scalars(select(m.Request).where(m.Request.day.between(first,last))))
    locks=s.scalar(select(func.count()).select_from(m.ScheduleLock).where(m.ScheduleLock.month_id.in_(mids)))
    derived=s.scalar(select(func.count()).select_from(m.HistoricalAssignment).where(m.HistoricalAssignment.month_id.in_(mids),cleared_history(months)))
    blocked=[];stamp=now();by_id={v.id:v for v in versions}
    if s.scalar(select(m.SolverRun.id).where(m.SolverRun.status.in_(['QUEUED','RUNNING','CANCELLING'])).limit(1)):
        blocked.append('Đang có Solver chạy; đợi hoàn tất hoặc hủy trước khi Clear năm.')
    for month in months:
        current=by_id.get(month.current_version_id)
        if current and current.status in ('PUBLISHED','ACTUAL'):
            blocked.append(f'Tháng {month.month}/{year} đã công bố hoặc chốt thực tế; không Clear lịch này.')
        if current and date(year,month.month,1)-timedelta(days=1)<=stamp.astimezone(LOCAL_TZ).date():
            data,_=load_month(s,month.id)
            if any(shift_start(data,d,c)<=stamp for (i,d),c in codes_for(s,current.id).items()):
                blocked.append(f'Tháng {month.month}/{year} đã có ca/ngày bắt đầu; không Clear lịch đã thực hiện.')
    has_data=bool(any(month.current_version_id or month.baseline_version_id for month in months) or
        vids-archived or jobs or locks or derived or any(r.status=='APPLIED' for r in requests) or
        any(p.baseline_frozen or p.status!='DRAFT' for p in plans))
    preview=dict(year=year,plan_id=plan_id,revision=revision(s),months=len(months),
        current_months=sum(bool(month.current_version_id) for month in months),
        baseline_months=sum(bool(month.baseline_version_id) for month in months),
        versions_to_archive=len(vids-archived),jobs_to_archive=len(jobs),locks_to_clear=locks,
        requests_kept=len(requests),requests_to_approve=sum(r.status=='APPLIED' for r in requests),
        request_statuses=dict(Counter(r.status for r in requests)),
        can_clear=has_data and not blocked,empty=not has_data,blocked_reasons=blocked,
        last_backup=previous.payload.get('backup_path') if previous else None)
    return preview,months,plans,versions,jobs,requests


def preview_clear(service,plan_id):
    with service.sessions() as s:return inspect_clear(s,plan_id)[0]


def clear_year(service,plan_id,payload):
    with service.sessions.begin() as s:
        # Reserve the write transaction BEFORE backup so its committed snapshot
        # is exactly the input state being cleared, including the WAL.
        s.execute(update(m.State).where(m.State.id==1).values(revision=m.State.revision))
        if revision(s)!=payload.revision:raise Conflict('Dữ liệu đã đổi; mở lại hộp Clear năm trước khi thực hiện.')
        preview,months,plans,versions,jobs,requests=inspect_clear(s,plan_id)
        if payload.year!=preview['year']:raise ValueError('Năm xác nhận không khớp kế hoạch; không Clear.')
        if preview['blocked_reasons']:raise Conflict(' '.join(preview['blocked_reasons']))
        if preview['empty']:return dict(revision=revision(s),year=payload.year,cleared=False,backup_path=preview['last_backup'])
        stamp=now();year=payload.year
        path=ROOT/'artifacts'/'backups'/f'before_clear_{year}_{stamp.astimezone(LOCAL_TZ):%Y%m%d_%H%M%S}_{uuid4().hex[:8]}.db'
        backup(Path(service.engine.url.database),path)
        old_months=[dict(id=x.id,month=x.month,current_version_id=x.current_version_id,baseline_version_id=x.baseline_version_id) for x in months]
        old_plans=[dict(id=p.id,status=p.status,baseline_frozen=p.baseline_frozen) for p in plans]
        bump(s,payload.revision)
        for month in months:month.current_version_id=month.baseline_version_id=None
        for plan in plans:plan.status,plan.baseline_frozen='DRAFT',False
        mids=[x.id for x in months]
        s.execute(delete(m.ScheduleLock).where(m.ScheduleLock.month_id.in_(mids)))
        # Remove only live derived history from inside the cleared year. The
        # previous year's anchor and immutable snapshots remain untouched.
        s.execute(delete(m.HistoricalAssignment).where(m.HistoricalAssignment.month_id.in_(mids),
            cleared_history(months)))
        for request in requests:
            if request.status!='APPLIED':continue
            request.status,request.revision='APPROVED',request.revision+1
            s.add(m.RequestRevision(request_id=request.id,revision=request.revision,
                payload={'status':'APPROVED','action':'CLEAR_YEAR','year':year,'reason':'Giữ đăng ký để lập lại lịch từ đầu.'}))
        for job in jobs:
            job.result={**(job.result or {}),'archived_by_clear':dict(year=year,revision=revision(s),
                previous_status=job.status,previous_stage=job.stage,at=stamp.isoformat())}
            job.status,job.stage,job.cancel_requested='ARCHIVED',f'Đã lưu trữ khi Clear năm {year}',True
        audit(s,'CLEAR_YEAR',year,dict(year=year,revision=revision(s),backup_path=str(path),
            months=old_months,plans=old_plans,archived_version_ids=[v.id for v in versions],
            archived_job_ids=[j.id for j in jobs],requests_reapproved=preview['requests_to_approve'],
            locks_removed=preview['locks_to_clear']))
        s.flush()
        return dict(revision=revision(s),year=year,cleared=True,backup_path=str(path),
            cleared_months=preview['current_months'],archived_versions=preview['versions_to_archive'],
            requests_kept=preview['requests_kept'],requests_reapproved=preview['requests_to_approve'])
