"""Capture an existing year's immutable inputs for draft-only rebalancing."""
from datetime import date,timedelta
from uuid import uuid4
from sqlalchemy import select,update
from . import models as m
from .db import revision,audit
from .repository import require,snapshot
from .domain import Conflict,now


def enqueue_balance(service,payload):
    from .services import row_dict
    with service.sessions.begin() as s:
        s.execute(update(m.State).where(m.State.id==1).values(revision=m.State.revision))
        if revision(s)!=payload.revision:raise Conflict('Đầu vào đã đổi; tải lại trước khi cân năm.')
        if s.scalar(select(m.SolverRun.id).where(m.SolverRun.status.in_(['QUEUED','RUNNING','CANCELLING']))):
            raise Conflict('Đang có Solver trên workspace.')
        plan=require(s,m.AnnualPlan,payload.annual_plan_id)
        months=s.scalars(select(m.PlanningMonth).where(m.PlanningMonth.annual_plan_id==plan.id).order_by(m.PlanningMonth.month)).all()
        if not months or any(not month.current_version_id for month in months):
            raise ValueError('Cân năm cần lịch đã áp dụng của mọi tháng trong kế hoạch. Lập và duyệt V0 trước.')
        if [month.month for month in months]!=list(range(months[0].month,months[-1].month+1)):
            raise ValueError('Các tháng cân năm phải liên tiếp.')
        if s.scalar(select(m.Request.id).where(m.Request.status=='APPROVED',m.Request.day.between(date(plan.year,months[0].month,1),date(plan.year+1,1,1)-timedelta(days=1))).limit(1)):
            raise Conflict('Còn yêu cầu đã duyệt chưa áp dụng. Dùng REPAIR áp dụng yêu cầu trước khi cân năm.')
        job_id=str(uuid4());snapshots=[]
        neighbor=s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==plan.year+1,m.PlanningMonth.month==1)) if months[-1].month==12 else s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==plan.year,m.PlanningMonth.month==months[-1].month+1))
        frontier=neighbor if neighbor and neighbor.current_version_id else None
        for month in months+([frontier] if frontier else []):
            locks=[dict(employee_id=l.employee_id,day=str(l.day)) for l in s.scalars(select(m.ScheduleLock).where(m.ScheduleLock.month_id==month.id))]
            context=dict(current_version=month.current_version_id,baseline=month.current_version_id,request_ids=[],
                locks=locks,cascade_job_id=job_id,frontier=month is frontier,options=payload.model_dump(mode='json'))
            snapshots.append(snapshot(s,month.id,context=context).id)
        first_allowed=date(plan.year,payload.balance_from_month,1)
        if payload.allowed_from:first_allowed=max(first_allowed,payload.allowed_from)
        options={**payload.model_dump(mode='json'),'cascade':True,'as_of':(payload.as_of or now()).isoformat(),
            'allowed_from':str(first_allowed),'allowed_until':str(payload.allowed_until) if payload.allowed_until else None,
            'baseline_version_id':months[0].current_version_id,'cascade_job_id':job_id}
        run=m.SolverRun(id=job_id,scope_key='workspace',month_id=months[0].id,annual_plan_id=plan.id,
            mode='BALANCE',status='QUEUED',revision=revision(s),options=options,snapshots=snapshots)
        s.add(run);s.flush();audit(s,'YEAR_BALANCE_QUEUED',job_id,options)
        return row_dict(run)
