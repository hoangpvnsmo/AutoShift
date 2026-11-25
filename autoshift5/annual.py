"""Rolling-horizon inputs, atomic V0 acceptance and cumulative statistics."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from sqlalchemy import select, delete
from . import models as m
from .domain import Conflict, deserialize, history_length, now
from .db import bump, revision, audit
from .repository import require, load_month, codes_for
from .services import row_dict
from .validation import validate_month, boundary_report, short_events
from core.data.calendar_utils import month_days, standard_credit
from core.validation.credit_checks import demand_requirements


def create_plan(service, payload):
    numbers = sorted(set(payload.months))
    if len(numbers)!=len(payload.months) or any(n<1 or n>12 for n in numbers) or numbers != list(range(numbers[0], numbers[-1]+1)):
        raise ValueError('Tháng cần liên tiếp, không trùng, trong 1..12.')
    with service.sessions.begin() as s:
        bump(s, payload.revision)
        source = require(s, m.PlanningMonth, payload.source_month_id)
        data, _ = load_month(s, source.id)
        original = codes_for(s, source.current_version_id) if source.current_version_id else {}
        if s.scalar(select(m.PlanningMonth.id).where(m.PlanningMonth.year==payload.year, m.PlanningMonth.month.in_(numbers))):
            raise Conflict('Đã có tháng trong năm này. Không ghi đè hoặc chuyển kế hoạch ngầm.')
        plan = m.AnnualPlan(name=payload.name, year=payload.year, assumptions=payload.assumptions)
        s.add(plan)
        s.flush()
        for j, number in enumerate(numbers):
            rules = deepcopy(data.rules)
            # Dated exceptions are not carried to different calendar dates.
            rules.setdefault('credit_policy', {})['daily_demand'] = {}
            profile = m.RuleProfile(name=f'Kế hoạch {plan.id} tháng {number}', revision=1, rules=rules)
            s.add(profile)
            s.flush()
            month = m.PlanningMonth(year=payload.year, month=number, annual_plan_id=plan.id, rule_profile_id=profile.id,
                input_confirmed=False, assumptions=payload.assumptions+'; Sao chép nhân sự/quyền ca/công chuẩn; chưa khai báo lễ/yêu cầu tương lai.')
            s.add(month)
            s.flush()
            days = month_days(payload.year, number)
            for pos, e in enumerate(data.employees):
                s.add(m.MonthlySetting(month_id=month.id, employee_id=e.id, position=pos, name=e.name, group=e.group,
                                      is_leader=e.is_leader, target_credit=e.target_credit))
                s.add_all(m.Permission(employee_id=e.id, shift=k, valid_from=days[0], valid_until=days[-1]) for k in e.allowed_shifts)
            if j==0:
                needed = {(e.id, days[0]-timedelta(days=k)) for e in data.employees for k in range(1, history_length(data)+1)}
                history = {**data.previous, **original}
                if not needed<=set(history):
                    raise ValueError('Thiếu lịch sử thực tế ngay trước tháng đầu. Chọn giai đoạn nối tiếp lịch nguồn hoặc nhập đủ lịch sử trước; không tự điền OFF.')
                s.add_all(m.HistoricalAssignment(month_id=month.id, employee_id=i, day=d, code=history[i,d]) for i,d in sorted(needed))
            target_data = replace(data, year=payload.year, month=number, rules=rules, holidays=frozenset(), requests=())
            s.add_all(m.Demand(month_id=month.id, day=d, shift=k, count=n) for (d,k),n in demand_requirements(target_data).items())
        audit(s, 'ANNUAL_PLAN', plan.id, payload.model_dump(mode='json'))
        return dict(revision=revision(s), plan=row_dict(plan))


def confirm_plan(service, plan_id, version_ids, expected_revision):
    """All months become the immutable V0 in one transaction; never auto-publish."""
    with service.sessions.begin() as s:
        bump(s, expected_revision)
        plan = require(s, m.AnnualPlan, plan_id)
        if plan.baseline_frozen:
            raise Conflict('V0 năm đã được khóa, không thay thế.')
        months = s.scalars(select(m.PlanningMonth).where(m.PlanningMonth.annual_plan_id==plan_id).order_by(m.PlanningMonth.month)).all()
        versions = [require(s, m.ScheduleVersion, vid) for vid in version_ids]
        if len(versions)!=len(months) or {v.month_id for v in versions}!={mo.id for mo in months}:
            raise ValueError('Cần đúng một bản nháp cho mọi tháng trong kế hoạch.')
        by_month = {v.month_id:v for v in versions}
        checked = []
        for month in months:
            v = by_month[month.id]
            if v.status!='DRAFT' or v.input_revision!=expected_revision:
                raise Conflict('Bản nháp không còn cùng revision đầu vào. Chạy lại cả kế hoạch.')
            snap = require(s, m.InputSnapshot, v.snapshot_id)
            data,prefs = deserialize(snap.payload)
            codes = codes_for(s, v.id)
            report = validate_month(data,codes,prefs,snap.work_policy)
            if checked:
                boundary = boundary_report(checked[-1][0],checked[-1][1],data,codes,prefs=prefs,work_policy=snap.work_policy)
                if boundary['errors']:
                    raise ValueError('Lỗi biên; không duyệt một phần kế hoạch.')
            checked.append((data,codes,report))
            v.status,v.confirmed_at='PLANNED',now()
            month.current_version_id,month.baseline_version_id=v.id,v.id
            s.execute(delete(m.HistoricalAssignment).where(m.HistoricalAssignment.month_id==month.id))
            s.add_all(m.HistoricalAssignment(month_id=month.id,employee_id=i,day=d,code=c) for (i,d),c in data.previous.items())
            for r in s.scalars(select(m.Request).where(m.Request.day.between(date(data.year,data.month,1),month_days(data.year,data.month)[-1]),m.Request.status=='APPROVED')):
                if codes.get((r.employee_id,r.day))==r.code:
                    r.status,r.revision='APPLIED',r.revision+1
                    s.add(m.RequestRevision(request_id=r.id,revision=r.revision,payload={'status':'APPLIED','version_id':v.id}))
        plan.baseline_frozen=True
        plan.status='PLANNED_INCOMPLETE' if any(r.hard_pending for _,_,r in checked) else 'PLANNED'
        audit(s,'ANNUAL_V0_FROZEN',plan.id,{'versions':version_ids})
        return dict(revision=revision(s),status=plan.status,version_ids=version_ids)


def annual_statistics(service, plan_id):
    from collections import Counter
    from types import SimpleNamespace
    from .year_metrics import YearMetrics,weekend_records
    from .validation import shift_start
    with service.sessions() as s:
        plan=require(s,m.AnnualPlan,plan_id)
        months=s.scalars(select(m.PlanningMonth).where(m.PlanningMonth.annual_plan_id==plan_id).order_by(m.PlanningMonth.month)).all()
        totals={}
        month_rows=[]
        personal=[]
        metric_frames=[];all_codes={}
        stamp=now()
        for month in months:
            vid=month.current_version_id
            month_rows.append({**row_dict(month),'pending_requests':s.scalar(select(__import__('sqlalchemy').func.count()).select_from(m.Request).where(m.Request.day.between(date(month.year,month.month,1),month_days(month.year,month.month)[-1]),m.Request.status=='APPROVED'))})
            if not vid:
                continue
            v=require(s,m.ScheduleVersion,vid)
            month_rows[-1]['snapshot_id']=v.snapshot_id
            month_rows[-1]['validation_status']=v.validation_status
            month_rows[-1]['version_status']=v.status
            data,_=deserialize(require(s,m.InputSnapshot,v.snapshot_id).payload)
            codes=codes_for(s,vid)
            all_codes.update(codes)
            metric_frames.append(SimpleNamespace(month_id=month.id,data=data,baseline=codes))
            baseline=codes_for(s,month.baseline_version_id) if month.baseline_version_id else codes
            changes=s.scalars(select(m.ScheduleChange).join(m.ScheduleVersion,m.ScheduleChange.version_id==m.ScheduleVersion.id).where(
                m.ScheduleVersion.month_id==month.id,m.ScheduleVersion.status.in_(['PLANNED','PUBLISHED','ACTUAL']))).all()
            days=month_days(data.year,data.month)
            short=short_events(data,codes)
            for e in data.employees:
                row=totals.setdefault(e.id,dict(employee_id=e.id,name=e.name,group=e.group,allowed_shifts=list(e.allowed_shifts),
                    shift_1=0,shift_2=0,shift_3=0,holiday=0,weekend=0,full_weekends=0,overtime=0,short_rest=0,changes=0,
                    substitutions=0,baseline_differences=0,work=0))
                row.setdefault('elapsed_shifts',0)
                count=Counter(codes[e.id,d] for d in days)
                for k in (1,2,3): row[f'shift_{k}']+=count[str(k)]
                row['holiday']+=sum(codes[e.id,d] in ('1','2','3') for d in days if d in data.holidays)
                row['weekend']+=sum(codes[e.id,d] in ('1','2','3') for d in days if d.weekday()>=5 and d not in data.holidays)
                row['elapsed_shifts']+=sum(codes[e.id,d] in ('1','2','3') and shift_start(data,d,codes[e.id,d])<=stamp for d in days)
                row['short_rest']+=sum(i==e.id for i,d in short)
                work=sum(data.rules['work_credit'][codes[e.id,d]] for d in days)
                row['work']+=work
                row['overtime']+=max(0,work-(standard_credit(data) if e.target_credit is None else e.target_credit))
                row['changes']+=sum(c.employee_id==e.id for c in changes)
                for c in changes:
                    if c.employee_id!=e.id or c.after not in ('1','2','3'):continue
                    changed_version=require(s,m.ScheduleVersion,c.version_id)
                    changed_snapshot=require(s,m.InputSnapshot,changed_version.snapshot_id)
                    direct={require(s,m.Request,rid).employee_id for rid in changed_snapshot.payload.get('_context',{}).get('request_ids',[])}
                    row['substitutions']+=int(e.id not in direct)
                row['baseline_differences']+=sum(codes[e.id,d]!=baseline[e.id,d] for d in days)
                for d in days:
                    personal.append(dict(employee_id=e.id,day=str(d),code=codes[e.id,d],version_id=vid))
                from core.objectives.rest_checks import rest_windows
                cfg=__import__('core.validation.transition_checks',fromlist=['transition_config']).transition_config(data)
                for sat,sun,pairs in rest_windows(data,days).weekends:
                    cell=lambda day: codes.get((e.id,day),data.previous.get((e.id,day)))
                    row['full_weekends']+=int(cell(sat)=='OFF' and cell(sun)=='OFF' and all(cell(d)!=str(k) for d,k in pairs))
        summary=None
        if metric_frames:
            last=month_days(metric_frames[-1].data.year,metric_frames[-1].data.month)[-1]+timedelta(days=1)
            neighbor=s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==last.year,m.PlanningMonth.month==last.month))
            frontier=None
            if neighbor and neighbor.current_version_id:
                fv=require(s,m.ScheduleVersion,neighbor.current_version_id)
                fd,_=deserialize(require(s,m.InputSnapshot,fv.snapshot_id).payload)
                frontier=SimpleNamespace(data=fd,baseline=codes_for(s,fv.id))
            metrics=YearMetrics(metric_frames,frontier);summary=metrics.report(all_codes)
            by_id={r['employee_id']:r for r in summary['employees']}
            known={**metrics.extra,**all_codes}
            for row in totals.values():
                i=row['employee_id'];row['total_shifts']=sum(row[f'shift_{k}'] for k in (1,2,3))
                row['weekend_nonholiday']=row['weekend']
                row['weekend']=sum(c in ('1','2','3') and d.weekday()>=5 for (eid,d),c in all_codes.items() if eid==i)
                # Reporting includes ALL known weekends, including the three
                # 2027 Sundays whose following Monday lies in the next month.
                row['full_weekends']=0
                for frame in metric_frames:
                    if not any(e.id==i for e in frame.data.employees):continue
                    for sat,sun,pairs,required in weekend_records(frame.data,known):
                        if all((i,d) in known for d in required):
                            row['full_weekends']+=int(known[i,sat]=='OFF' and known[i,sun]=='OFF' and all(known[i,d]!=shift for d,shift in pairs))
                for field in ('target_shifts_lower','target_shifts_upper','target_weekend_lower','target_weekend_upper',
                              'remaining_to_target_lower','remaining_to_target_upper'):
                    row[field]=None
                if i in by_id:
                    t=by_id[i]['targets']['total_shifts'];w=by_id[i]['targets']['weekend_shifts']
                    row.update(target_shifts_lower=t['lower'],target_shifts_upper=t['upper'],
                        target_weekend_lower=w['lower'],target_weekend_upper=w['upper'],
                        remaining_to_target_lower=max(0,t['lower']-row['elapsed_shifts']),
                        remaining_to_target_upper=max(0,t['upper']-row['elapsed_shifts']))
        return dict(plan=row_dict(plan),months=month_rows,employees=list(totals.values()),personal=personal,
                    balance_summary=summary,
                    fairness='Cân năm ưu tiên tổng ca 1/2/3, ca T7/CN kể cả lễ, nghỉ trọn theo giờ thực. Mục tiêu điều chỉnh theo ngày khả dụng và tỷ lệ công chuẩn; bảo vệ lịch thực tế và lịch đã công bố.')
