"""Capture a linked window and atomically approve its immutable versions."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, update
from core.data.calendar_utils import month_days
from . import models as m
from .db import revision, bump, audit
from .domain import Conflict, now, deserialize, history_gaps
from .repository import require, snapshot, codes_for, load_month
from .validation import validate_month, boundary_report, shift_start
from .cascade_model import previous_with


def enqueue_cascade(service,payload):
    from .services import row_dict
    if payload.mode not in ('FRESH','REPAIR'):raise ValueError('Sửa liên kết dùng FRESH hoặc REPAIR.')
    with service.sessions.begin() as s:
        s.execute(update(m.State).where(m.State.id==1).values(revision=m.State.revision))
        if revision(s)!=payload.revision:raise Conflict('Đầu vào đã đổi; tải lại trước khi chạy.')
        if s.scalar(select(m.SolverRun.id).where(m.SolverRun.status.in_(['QUEUED','RUNNING','CANCELLING']))):
            raise Conflict('Đang có Solver trên workspace.')
        root=require(s,m.PlanningMonth,payload.month_id)
        if payload.mode=='REPAIR':
            if not root.current_version_id:raise ValueError('REPAIR liên kết cần lịch áp dụng làm tham chiếu.')
            if payload.baseline_version_id and payload.baseline_version_id!=root.current_version_id:
                raise ValueError('REPAIR liên kết cần chọn phiên bản đang áp dụng làm tham chiếu.')
            if payload.as_of is None:raise ValueError('REPAIR cần as_of có múi giờ.')
        months=[root];frontier=None;last=root
        for step in range(payload.cascade_months+1):
            idx=last.year*12+last.month
            year,mon=divmod(idx,12);mon+=1
            other=s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==year,m.PlanningMonth.month==mon))
            if not other or not other.current_version_id:break
            if step==payload.cascade_months:frontier=other;break
            months.append(other);last=other
        selected=[require(s,m.Request,rid) for rid in set(payload.request_ids)]
        pairs={(x.year,x.month) for x in months}
        if any((r.day.year,r.day.month) not in pairs or r.status not in ('APPROVED','APPLIED','CANCELLED') for r in selected):
            raise ValueError('Yêu cầu được chọn cần thuộc vùng tháng liên kết và có trạng thái đã duyệt/áp dụng/hủy.')
        if payload.mode=='REPAIR':
            days=month_days(root.year,root.month)
            pending=set(s.scalars(select(m.Request.id).where(m.Request.day.between(days[0],days[-1]),m.Request.status=='APPROVED')))
            if not pending<=set(payload.request_ids):raise Conflict('Cần chọn tất cả yêu cầu đã duyệt chưa áp dụng trong tháng gốc.')
        job_id=str(uuid4());snaps=[]
        for month in months+([frontier] if frontier else []):
            days=month_days(month.year,month.month)
            ids=list(s.scalars(select(m.Request.id).where(m.Request.day.between(days[0],days[-1]),m.Request.status=='APPROVED')))
            ids+= [r.id for r in selected if r.day.year==month.year and r.day.month==month.month and r.id not in ids]
            locks=[dict(employee_id=l.employee_id,day=str(l.day)) for l in s.scalars(select(m.ScheduleLock).where(m.ScheduleLock.month_id==month.id))]
            context=dict(current_version=month.current_version_id,baseline=month.current_version_id,
                request_ids=ids,locks=locks,cascade_job_id=job_id,frontier=month is frontier,
                options=payload.model_dump(mode='json'))
            snaps.append(snapshot(s,month.id,context=context).id)
        options={**payload.model_dump(mode='json'),'as_of':(payload.as_of or now()).isoformat(),
            'baseline_version_id':root.current_version_id,'cascade_job_id':job_id}
        job=m.SolverRun(id=job_id,scope_key='workspace',month_id=root.id,mode=payload.mode,
            status='QUEUED',revision=revision(s),options=options,snapshots=snaps)
        s.add(job);s.flush();audit(s,'CASCADE_QUEUED',job.id,options)
        return row_dict(job)


def confirm_cascade(service,job_id,payload):
    with service.sessions.begin() as s:
        bump(s,payload.revision)
        job=require(s,m.SolverRun,job_id)
        if not job.options.get('cascade') or job.status!='SUCCEEDED' or not job.result or job.result.get('confirmed'):
            raise Conflict('Gói chưa có kết quả để duyệt hoặc đã được duyệt.')
        if job.revision!=payload.revision:raise Conflict('Đầu vào đã đổi; cần chạy lại gói.')
        ids=job.result['version_ids'];entries=[];flat={};stamp=now()
        for vid in ids:
            v=require(s,m.ScheduleVersion,vid);month=require(s,m.PlanningMonth,v.month_id)
            snap=require(s,m.InputSnapshot,v.snapshot_id);ctx=snap.payload.get('_context',{})
            if v.status!='DRAFT' or v.input_revision!=payload.revision or ctx.get('cascade_job_id')!=job_id:
                raise Conflict('Thành viên gói không còn là bản nháp hợp lệ.')
            if month.current_version_id!=ctx['current_version']:
                raise Conflict('Lịch áp dụng của một tháng trong gói đã đổi.')
            if not entries and month.current_version_id!=payload.expected_current_version_id:
                raise Conflict('Lịch gốc đã đổi.')
            codes=codes_for(s,vid);data,prefs=load_month(s,month.id);data=previous_with(data,flat)
            profile=require(s,m.RuleProfile,month.rule_profile_id)
            report=validate_month(data,codes,prefs,profile.work_policy)
            old=codes_for(s,month.current_version_id) if month.current_version_id else {}
            oldv=require(s,m.ScheduleVersion,month.current_version_id) if month.current_version_id else None
            locks={(l.employee_id,l.day) for l in s.scalars(select(m.ScheduleLock).where(m.ScheduleLock.month_id==month.id))}
            for k,c in codes.items():
                if c!=old.get(k) and (k in locks or (oldv and (oldv.status=='ACTUAL' or shift_start(data,k[1],old[k])<=stamp)) or shift_start(data,k[1],c)<=stamp):
                    raise Conflict('Gói đụng lịch thực tế, ô khóa hoặc ca/ngày đã bắt đầu; không áp dụng một phần.')
            status=payload.status if not entries else (oldv.status if oldv and oldv.status in ('PUBLISHED','ACTUAL') else payload.status)
            if status in ('PUBLISHED','ACTUAL') and (report.hard_pending or history_gaps(data)):
                raise Conflict('Chưa đủ giờ phi ca/lịch sử để công bố hoặc chốt toàn gói.')
            if entries:
                last=entries[-1]
                if boundary_report(last['data'],last['codes'],data,codes,prefs=prefs,work_policy=profile.work_policy)['errors']:
                    raise Conflict('Biên trong gói không hợp lệ.')
            entries.append(dict(v=v,month=month,snap=snap,data=data,codes=codes,status=status));flat.update(codes)
        last=entries[-1];days=month_days(last['data'].year,last['data'].month)
        nextday=days[-1]+timedelta(days=1)
        neighbor=s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==nextday.year,m.PlanningMonth.month==nextday.month))
        if neighbor and neighbor.current_version_id:
            v=require(s,m.ScheduleVersion,neighbor.current_version_id);snap=require(s,m.InputSnapshot,v.snapshot_id)
            data,prefs=deserialize(snap.payload)
            if boundary_report(last['data'],last['codes'],data,codes_for(s,v.id),prefs=prefs,work_policy=snap.work_policy)['errors']:
                raise Conflict('Gói mâu thuẫn với tháng tiếp theo đang giữ nguyên.')
        # Mutation only follows successful validation of the entire package.
        for e in entries:
            v,month=e['v'],e['month'];v.status,v.confirmed_at=e['status'],stamp;month.current_version_id=v.id
            for rid in e['snap'].payload['_context'].get('request_ids',[]):
                req=require(s,m.Request,rid)
                if req.status=='APPROVED' and e['codes'].get((req.employee_id,req.day))==req.code:
                    req.status,req.revision='APPLIED',req.revision+1
                    s.add(m.RequestRevision(request_id=req.id,revision=req.revision,payload={'status':'APPLIED','version_id':v.id}))
        job.result={**job.result,'confirmed':True,'confirmed_at':stamp.isoformat()}
        audit(s,'CONFIRM_CASCADE',job.id,{'version_ids':ids})
        return dict(revision=revision(s),version_ids=ids,status='CONFIRMED')
