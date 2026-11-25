"""Durable linked-month job: solve outside transactions, save all or none."""
from datetime import date, datetime
import traceback
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker
from . import models as m
from .db import revision, audit
from .domain import now, deserialize, serialize, Conflict, changed_cells
from .repository import require, codes_for, snapshot, save_version
from .repair import RepairOptions
from .cascade_model import CascadeFrame
from .cascade import solve_cascade


def execute_cascade(engine,job_id):
    from .worker import Cancelled, NoSolution
    sessions=sessionmaker(engine,expire_on_commit=False)
    try:
        with sessions.begin() as s:
            job=require(s,m.SolverRun,job_id)
            if job.cancel_requested or job.status=='CANCELLED':
                job.status,job.finished_at='CANCELLED',now();return
            job.status,job.started_at='RUNNING',now()
            opts=dict(job.options);rev=job.revision;mode=job.mode;frames=[];frontier=None;direct=set()
            for sid in job.snapshots:
                snap=require(s,m.InputSnapshot,sid);ctx=snap.payload['_context'];data,prefs=deserialize(snap.payload)
                vid=ctx['current_version'];v=require(s,m.ScheduleVersion,vid) if vid else None
                fixed=require(s,m.InputSnapshot,v.snapshot_id) if v else None
                fd,fp=deserialize(fixed.payload) if fixed else (None,())
                month=require(s,m.PlanningMonth,snap.month_id)
                f=CascadeFrame(month_id=snap.month_id,data=data,prefs=prefs,baseline=codes_for(s,vid) if vid else {},
                    version_id=vid,status=v.status if v else 'PLANNED',
                    locks=tuple((l['employee_id'],date.fromisoformat(l['day'])) for l in ctx['locks']),
                    annual=codes_for(s,month.baseline_version_id) if month.baseline_version_id else None,
                    work_policy=snap.work_policy,fixed_data=fd,fixed_prefs=fp,
                    fixed_policy=fixed.work_policy if fixed else None,snapshot_id=sid)
                if ctx.get('frontier'):frontier=f
                else:
                    frames.append(f)
                    for rid in ctx['request_ids']:direct.add(require(s,m.Request,rid).employee_id)
            selected=[require(s,m.Request,rid) for rid in opts.get('request_ids',[])]
            requested_months={(r.day.year,r.day.month) for r in selected}
            minimum_size=max([1]+[index+1 for index,f in enumerate(frames) if (f.data.year,f.data.month) in requested_months])
            # Changed future hard inputs cannot be bypassed by freezing their old
            # schedule under an old snapshot. This also makes expanding windows
            # true relaxations of the same mandatory-input problem.
            for index,f in enumerate(frames[1:],1):
                current=serialize(f.data,());fixed=serialize(f.fixed_data,())
                for raw in (current,fixed):raw.pop('previous',None)
                if current!=fixed or f.work_policy!=f.fixed_policy:
                    minimum_size=max(minimum_size,index+1)
        def cancelled():
            with sessions() as s:
                j=s.get(m.SolverRun,job_id)
                return not j or j.cancel_requested or j.status!='RUNNING'
        def emit(*parts):
            with sessions.begin() as s:
                j=require(s,m.SolverRun,job_id)
                if j.cancel_requested or j.status!='RUNNING':raise Cancelled()
                msg=' '.join(map(str,parts));j.log=(j.log+'\n'+msg)[-200000:];j.stage=msg[:150]
        options=RepairOptions(as_of=datetime.fromisoformat(opts['as_of']),budget=opts['budget'],mode=opts['repair_mode'],
            allowed_from=date.fromisoformat(opts['allowed_from']) if opts.get('allowed_from') else None,
            allowed_until=date.fromisoformat(opts['allowed_until']) if opts.get('allowed_until') else None,
            max_changes=opts.get('max_changes'),no_admin_increase=opts['no_admin_increase'],
            no_overtime_increase=opts['no_overtime_increase'],disabled_layers=tuple(opts.get('disabled_layers',[])))
        if minimum_size>1:emit(f'Đầu vào/yêu cầu tương lai đã đổi: vùng giải tối thiểu {minimum_size} tháng.')
        if mode=='BALANCE':
            from .year_balance import balance_year
            result=balance_year(frames,frontier,options,window_months=opts.get('balance_window_months',3),emit=emit,cancelled=cancelled)
        else:
            result=solve_cascade(frames,frontier,options,root_fresh=mode=='FRESH',direct_ids=direct,minimum_size=minimum_size,emit=emit,cancelled=cancelled)
        if result.status=='CANCELLED' or cancelled():raise Cancelled()
        if not result.entries:raise NoSolution(result.status,'Không tìm được gói sửa hợp lệ; không lưu hoặc đổi lịch áp dụng.')
        with sessions.begin() as s:
            s.execute(update(m.State).where(m.State.id==1).values(revision=m.State.revision))
            if revision(s)!=rev:raise Conflict('Đầu vào đã thay đổi trong lúc giải; không lưu gói.')
            j=require(s,m.SolverRun,job_id)
            if j.cancel_requested or j.status!='RUNNING':raise Cancelled()
            # Check every captured pointer, including the fixed frontier.
            for f in frames+([frontier] if frontier else []):
                if require(s,m.PlanningMonth,f.month_id).current_version_id!=f.version_id:
                    raise Conflict('Lịch áp dụng trong vùng liên kết đã đổi; không lưu gói.')
            ids=[];summaries=[]
            for index,e in enumerate(result.entries):
                f=e['frame'];original=require(s,m.InputSnapshot,f.snapshot_id)
                context={**original.payload['_context'],'cascade_job_id':job_id}
                snap=snapshot(s,f.month_id,data=e['data'],prefs=f.prefs,context=context)
                v=save_version(s,f.month_id,snap,e['codes'],e['report'].to_dict(),source=mode if index==0 or mode=='BALANCE' else 'REPAIR_CASCADE',
                    parent=f.version_id,proof={**result.proof,'cascade_job_id':job_id},scores=e['quality'],repair=e['metrics'])
                changes=changed_cells(f.baseline,e['codes']) if f.baseline else []
                ids.append(v.id);summaries.append(dict(month_id=f.month_id,year=f.data.year,month=f.data.month,
                    version_id=v.id,before_version_id=f.version_id,changed_cells=len(changes),
                    affected_people=len({c['employee_id'] for c in changes}),new_month=not bool(f.baseline),
                    validation_status=e['report'].verdict,counted_in_stability=not(mode=='FRESH' and index==0)))
            j.status,j.finished_at,j.stage='SUCCEEDED',now(),'Đã lưu gói bản nháp liên kết; chờ duyệt cùng nhau'
            j.result=dict(cascade=True,version_ids=ids,month_summaries=summaries,repair=result.metrics,proof=result.proof,
                attempts=result.attempts,boundaries=result.boundaries,publication='DRAFT_ONLY',confirmed=False)
            if mode=='BALANCE':j.result['annual_balance']=result.metrics['annual_balance']
            audit(s,'CASCADE_RESULT',job_id,{'version_ids':ids,'changed_cells':result.metrics['changed_cells']})
    except Exception as exc:
        with sessions.begin() as s:
            j=require(s,m.SolverRun,job_id)
            j.status='CANCELLED' if isinstance(exc,Cancelled) else 'STALE' if isinstance(exc,Conflict) else exc.status if isinstance(exc,NoSolution) else 'FAILED'
            j.error=None if isinstance(exc,Cancelled) else str(exc)
            j.finished_at=now();j.stage='Không áp dụng lịch: '+j.status
            j.result={'version_ids':[],'cascade':True}
            j.log=(j.log+'\n'+traceback.format_exc())[-200000:]
