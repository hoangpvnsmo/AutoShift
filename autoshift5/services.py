"""Application use cases. Transactions are short; optimization happens elsewhere."""
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import select, delete
from sqlalchemy.orm import sessionmaker
from core.data.calendar_utils import month_days
from core.validation.credit_checks import demand_requirements
from . import models as m
from .domain import Conflict, Unsupported, deserialize, history_gaps, now, CODES, LOCAL_TZ
from .db import bump, revision, audit
from .repository import require, load_month, codes_for, snapshot, save_version, validation_for
from .validation import validate_month, boundary_report, frozen_cell


def row_dict(row):
    result={c.name: getattr(row, c.name) for c in row.__table__.columns}
    for key,value in result.items():
        if isinstance(value,datetime):
            result[key]=(value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(LOCAL_TZ)
    return result


class Service:
    def __init__(self, engine):
        self.engine = engine
        self.sessions = sessionmaker(engine, expire_on_commit=False)

    def state(self):
        with self.sessions() as s:
            return dict(revision=revision(s), version='5.0.0')

    def months(self):
        with self.sessions() as s:
            return [row_dict(x) for x in s.scalars(select(m.PlanningMonth).order_by(m.PlanningMonth.year, m.PlanningMonth.month))]

    def calendar(self, month_id, version_id=None):
        with self.sessions() as s:
            month = require(s, m.PlanningMonth, month_id)
            current_data, prefs = load_month(s, month_id)
            selected = version_id or month.current_version_id
            v = require(s, m.ScheduleVersion, selected) if selected else None
            if v and v.month_id != month_id:
                raise ValueError('Phiên bản không thuộc tháng.')
            data = deserialize(require(s, m.InputSnapshot, v.snapshot_id).payload)[0] if v else current_data
            days = month_days(data.year, data.month)
            req = s.scalars(select(m.Request).where(m.Request.day.between(days[0], days[-1]),
                        m.Request.status.notin_(['REJECTED', 'CANCELLED'])).order_by(m.Request.day, m.Request.employee_id)).all()
            locks = s.scalars(select(m.ScheduleLock).where(m.ScheduleLock.month_id == month_id)).all()
            return dict(revision=revision(s), month=row_dict(month), days=[dict(date=str(d), day=d.day, weekday=d.weekday(), holiday=d in data.holidays) for d in days],
                employees=[asdict(e) for e in data.employees], version=row_dict(v) if v else None,
                assignments=[dict(employee_id=i, day=str(d), code=c) for (i, d), c in codes_for(s, v.id).items()] if v else [],
                requests=[row_dict(r) for r in req],
                cancelled_requests=[row_dict(r) for r in s.scalars(select(m.Request).where(m.Request.day.between(days[0],days[-1]),m.Request.status=='CANCELLED'))],
                preferences=[asdict(p) for p in prefs], locks=[row_dict(l) for l in locks],
                demands=[dict(day=str(d), shift=k, count=n) for (d, k), n in demand_requirements(data).items()],
                validation=validation_for(s, v.id) if v else None, history_gaps=len(history_gaps(current_data)),
                work_credit=data.rules['work_credit'], min_leaders_per_shift=data.rules['min_leaders_per_shift'],
                versions=[row_dict(x) for x in s.scalars(select(m.ScheduleVersion).where(m.ScheduleVersion.month_id == month_id).order_by(m.ScheduleVersion.created_at.desc()))])

    def requests(self):
        with self.sessions() as s:
            return [row_dict(r) for r in s.scalars(select(m.Request).order_by(m.Request.day, m.Request.employee_id))]

    def request_batch(self, payload):
        if payload.code == 'T':
            raise Unsupported('Core 4.9 chỉ sinh T cho ADMIN; yêu cầu cứng T chưa được hỗ trợ. Không lưu như rule có hiệu lực.')
        with self.sessions.begin() as s:
            bump(s, payload.revision)
            ids = sorted(set(payload.employee_ids))
            for i in ids:
                require(s, m.Employee, i)
            conflicts = s.scalars(select(m.Request).where(m.Request.employee_id.in_(ids),
                 m.Request.day.between(payload.from_date, payload.to_date), m.Request.status.notin_(['REJECTED', 'CANCELLED']))).all()
            if {r.id for r in conflicts} != set(payload.replace_ids):
                raise Conflict('Yêu cầu xung đột; phải chỉ định chính xác replace_ids để hủy/thay thế: '+', '.join(r.id for r in conflicts))
            for r in conflicts:
                r.status = 'CANCELLED'
                r.revision += 1
                s.add(m.RequestRevision(request_id=r.id, revision=r.revision, payload={'status': 'CANCELLED', 'replaced': True}))
            s.flush()
            created = []
            for i in ids:
                d = payload.from_date
                while d <= payload.to_date:
                    month = s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year == d.year, m.PlanningMonth.month == d.month))
                    if month is None or s.get(m.MonthlySetting, (month.id, i)) is None:
                        raise ValueError(f'Chưa có kế hoạch/nhân sự ID {i} cho tháng {d.year}-{d.month}.')
                    rid = str(uuid4())
                    r = m.Request(id=rid, employee_id=i, day=d, code=payload.code, reason=payload.reason, status=payload.status, revision=1)
                    s.add(r)
                    s.flush()
                    s.add(m.RequestRevision(request_id=rid, revision=1, payload=dict(employee_id=i, day=str(d), code=payload.code, reason=payload.reason, status=payload.status)))
                    created.append(row_dict(r))
                    d += timedelta(days=1)
            for mid in {s.scalar(select(m.PlanningMonth.id).where(m.PlanningMonth.year==date.fromisoformat(str(r['day'])).year,
                        m.PlanningMonth.month==date.fromisoformat(str(r['day'])).month)) for r in created}:
                load_month(s, mid)  # Rights / ADMIN / fixed requests validated in the domain.
            audit(s, 'REQUEST_BATCH', ','.join(map(str,ids)), {'requests': [r['id'] for r in created], 'replaced': payload.replace_ids})
            return dict(revision=revision(s), requests=created)

    def request_transition(self, request_id, payload):
        transitions = {'DRAFT': {'SUBMITTED', 'CANCELLED'}, 'SUBMITTED': {'APPROVED', 'REJECTED', 'CANCELLED'},
                       'APPROVED': {'CANCELLED', 'REJECTED'}, 'APPLIED': {'CANCELLED'}, 'REJECTED': set(), 'CANCELLED': set()}
        with self.sessions.begin() as s:
            bump(s, payload.revision)
            r = require(s, m.Request, request_id)
            if r.revision != payload.request_revision or payload.status not in transitions[r.status]:
                raise Conflict('Revision/trạng thái yêu cầu không cho phép chuyển trạng thái này.')
            r.status = payload.status
            r.revision += 1
            s.add(m.RequestRevision(request_id=r.id, revision=r.revision, payload=row_dict(r)))
            s.flush()
            month = s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==r.day.year, m.PlanningMonth.month==r.day.month))
            load_month(s, month.id)
            audit(s, 'REQUEST_STATUS', r.id, {'status': r.status})
            return dict(revision=revision(s), request=row_dict(r))

    def set_preference(self, payload):
        with self.sessions.begin() as s:
            bump(s, payload.revision)
            month = s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==payload.day.year, m.PlanningMonth.month==payload.day.month))
            if month is None or s.get(m.MonthlySetting, (month.id, payload.employee_id)) is None:
                raise ValueError('Chưa có kế hoạch tháng/nhân sự.')
            p = s.scalar(select(m.Preference).where(m.Preference.employee_id==payload.employee_id, m.Preference.day==payload.day))
            if p is None:
                p = m.Preference(employee_id=payload.employee_id, day=payload.day)
                s.add(p)
            p.kind, p.shift, p.weight = payload.kind, payload.shift, payload.weight
            s.flush()
            load_month(s, month.id)
            audit(s, 'PREFERENCE', payload.employee_id, payload.model_dump(mode='json'))
            return dict(revision=revision(s), preference=row_dict(p))

    def employee(self, payload):
        if payload.group=='ADMIN' and (payload.is_leader or 1 in payload.allowed_shifts):
            raise ValueError('ADMIN không đi ca 1 hoặc làm phụ trách theo nghiệp vụ hiện tại.')
        if payload.group=='SHIFT' and not payload.allowed_shifts:
            raise ValueError('SHIFT cần ít nhất một quyền ca.')
        with self.sessions.begin() as s:
            bump(s, payload.revision)
            e = s.get(m.Employee, payload.id)
            if e is None:
                e = m.Employee(id=payload.id)
                s.add(e)
            e.name, e.group, e.is_leader = payload.name.strip(), payload.group, payload.is_leader
            s.flush()
            for mid in set(payload.month_ids):
                month = require(s, m.PlanningMonth, mid)
                days = month_days(month.year, month.month)
                setting = s.get(m.MonthlySetting, (mid, payload.id))
                if setting is None:
                    pos = len(s.scalars(select(m.MonthlySetting).where(m.MonthlySetting.month_id==mid)).all())
                    setting = m.MonthlySetting(month_id=mid, employee_id=e.id, position=pos)
                    s.add(setting)
                setting.name, setting.group, setting.is_leader, setting.target_credit = e.name, e.group, e.is_leader, payload.target_credit
                s.execute(delete(m.Permission).where(m.Permission.employee_id==e.id, m.Permission.valid_from==days[0], m.Permission.valid_until==days[-1]))
                s.add_all(m.Permission(employee_id=e.id, shift=k, valid_from=days[0], valid_until=days[-1]) for k in sorted(set(payload.allowed_shifts)))
                s.flush()
                load_month(s, mid)
            audit(s, 'EMPLOYEE', e.id, payload.model_dump(mode='json'))
            return dict(revision=revision(s), employee=row_dict(e))

    def rules(self, month_id, payload=None):
        with self.sessions.begin() as s:
            month = require(s, m.PlanningMonth, month_id)
            profile = require(s, m.RuleProfile, month.rule_profile_id)
            if payload is None:
                from validator.catalog import RULES
                return dict(profile=row_dict(profile), catalog=[asdict(r) for r in RULES], revision=revision(s))
            bump(s, payload.revision)
            rules = deepcopy(profile.rules)
            for key in ('max_consecutive_night_shifts', 'max_consecutive_shift_days', 'min_rest_hours', 'min_leaders_per_shift', 'min_previous_day_staff'):
                rules[key] = getattr(payload, key)
            for key in ('balance_shift_types', 'balance_night_shifts', 'balance_holiday_shifts', 'balance_weekend_shifts', 'balance_rest_days', 'minimize_shift_changes', 'honor_preferences'):
                rules.setdefault('optimization', {})[key] = getattr(payload, key)
            new = m.RuleProfile(name=f'Tháng {month_id} revision {revision(s)}', revision=revision(s), rules=rules, work_policy=profile.work_policy)
            s.add(new)
            s.flush()
            month.rule_profile_id = new.id
            s.flush()
            load_month(s, month_id)
            audit(s, 'RULE_REVISION', new.id, payload.model_dump(mode='json'))
            return dict(revision=revision(s), profile=row_dict(new))

    def calendar_config(self, month_id, payload):
        with self.sessions.begin() as s:
            bump(s, payload.revision)
            month = require(s, m.PlanningMonth, month_id)
            days = set(month_days(month.year, month.month))
            if len(payload.holidays) != len(set(payload.holidays)) or not set(payload.holidays)<=days:
                raise ValueError('Ngày lễ trùng hoặc ngoài tháng.')
            s.execute(delete(m.Holiday).where(m.Holiday.month_id==month_id))
            s.add_all(m.Holiday(month_id=month_id, day=d) for d in payload.holidays)
            seen = set()
            for row in payload.demands:
                row = row.model_dump()
                d, shift, count = date.fromisoformat(str(row['day'])), row['shift'], row['count']
                if d not in days or type(shift) is not int or shift not in (1, 2, 3) or type(count) is not int or (d, shift) in seen:
                    raise ValueError('Nhu cầu sai ngày/ca/số người hoặc trùng.')
                seen.add((d, shift))
                require(s, m.Demand, (month_id, d, shift)).count = count
            month.assumptions, month.input_confirmed = payload.assumptions, payload.input_confirmed
            s.flush()
            load_month(s, month_id)
            audit(s, 'CALENDAR_CONFIG', month_id, payload.model_dump(mode='json'))
            return dict(revision=revision(s))

    def lock(self, payload):
        with self.sessions.begin() as s:
            bump(s, payload.revision)
            month = require(s, m.PlanningMonth, payload.month_id)
            if payload.day not in month_days(month.year, month.month):
                raise ValueError('Ngày khóa ngoài tháng.')
            for i in set(payload.employee_ids):
                require(s, m.MonthlySetting, (month.id, i))
                obj = s.get(m.ScheduleLock, (month.id, i, payload.day))
                if payload.locked and obj is None:
                    s.add(m.ScheduleLock(month_id=month.id, employee_id=i, day=payload.day, reason=payload.reason))
                elif not payload.locked and obj is not None:
                    s.delete(obj)
            audit(s, 'LOCK', month.id, payload.model_dump(mode='json'))
            return dict(revision=revision(s))

    def compare(self, left, right):
        from .domain import changed_cells
        with self.sessions() as s:
            a, b = require(s, m.ScheduleVersion, left), require(s, m.ScheduleVersion, right)
            if a.month_id != b.month_id:
                raise ValueError('Chỉ so sánh phiên bản cùng tháng.')
            changes = changed_cells(codes_for(s, left), codes_for(s, right))
            return dict(changed_cells=len(changes), changes=changes, before=validation_for(s, left), after=validation_for(s, right))

    def confirm(self, version_id, payload):
        with self.sessions.begin() as s:
            bump(s, payload.revision)
            v = require(s, m.ScheduleVersion, version_id)
            month = require(s, m.PlanningMonth, v.month_id)
            if v.status != 'DRAFT':
                raise Conflict('Phiên bản đã xác nhận là bất biến. Khôi phục/chỉnh sửa bằng phiên bản con mới.')
            if month.current_version_id != payload.expected_current_version_id or v.input_revision != payload.revision:
                raise Conflict('Lịch áp dụng hoặc dữ liệu đầu vào đã đổi; chạy/kiểm tra lại bản nháp trước khi duyệt.')
            data, prefs = load_month(s, month.id)
            profile = require(s, m.RuleProfile, month.rule_profile_id)
            codes = codes_for(s, v.id)
            report = validate_month(data, codes, prefs, profile.work_policy)
            if month.current_version_id:
                from .validation import shift_start
                current=codes_for(s,month.current_version_id)
                current_version=require(s,m.ScheduleVersion,month.current_version_id)
                locks={(l.employee_id,l.day) for l in s.scalars(select(m.ScheduleLock).where(m.ScheduleLock.month_id==month.id))}
                for key,c in codes.items():
                    if key in current and c!=current[key] and (current_version.status=='ACTUAL' or key in locks or
                            shift_start(data,key[1],current[key])<=now() or shift_start(data,key[1],c)<=now()):
                        raise Conflict('Không áp dụng thay đổi lên lịch thực tế đã chốt, ô khóa hoặc ca/ngày đã bắt đầu.')
            self._check_neighbors(s, data, codes)
            if payload.status in ('PUBLISHED', 'ACTUAL') and (report.hard_pending or history_gaps(data)):
                raise Conflict('Chưa đủ giờ phi ca/lịch sử. Có thể duyệt PLANNED với nhãn INCOMPLETE; chưa thể công bố/chốt thực tế.')
            v.status, v.confirmed_at = payload.status, now()
            month.current_version_id = v.id
            snap = require(s, m.InputSnapshot, v.snapshot_id)
            for rid in snap.payload.get('_context', {}).get('request_ids', []):
                r = require(s, m.Request, rid)
                if r.status == 'APPROVED' and codes.get((r.employee_id, r.day)) == r.code:
                    r.status, r.revision = 'APPLIED', r.revision+1
                    s.add(m.RequestRevision(request_id=r.id, revision=r.revision, payload={'status': 'APPLIED', 'version_id': v.id}))
            audit(s, 'CONFIRM_VERSION', v.id, payload.model_dump(mode='json'))
            return dict(revision=revision(s), version_id=v.id, status=v.status)

    def _check_neighbors(self, s, data, codes):
        first, *_, last = month_days(data.year, data.month)
        reports = []
        for day, side in ((first-timedelta(days=1), 'left'), (last+timedelta(days=1), 'right')):
            other = s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==day.year, m.PlanningMonth.month==day.month))
            if not other or not other.current_version_id:
                continue
            v = require(s, m.ScheduleVersion, other.current_version_id)
            snap = require(s, m.InputSnapshot, v.snapshot_id)
            od, op = deserialize(snap.payload)
            oc = codes_for(s, v.id)
            r = boundary_report(od, oc, data, codes, prefs=(), work_policy=None) if side=='left' else boundary_report(data, codes, od, oc, prefs=op, work_policy=snap.work_policy)
            if r['errors']:
                raise Conflict('Lịch mới gây lỗi biên tháng '+r['left']+' / '+r['right']+'. Cần REPAIR nhiều tháng; trường hợp này chưa hỗ trợ, không lưu một phần.')
            reports.append(r)
        return reports

    def edit_draft(self, version_id, payload, *, restore=False):
        with self.sessions.begin() as s:
            bump(s, payload.revision)
            old = require(s, m.ScheduleVersion, version_id)
            if not restore and old.status != 'DRAFT':
                raise Conflict('Chỉ chỉnh sửa ô trên bản nháp. Dùng khôi phục để tạo bản con từ lịch đã xác nhận.')
            data, prefs = load_month(s, old.month_id)
            month = require(s, m.PlanningMonth, old.month_id)
            current = codes_for(s, month.current_version_id) if month.current_version_id else codes_for(s, old.id)
            codes = codes_for(s, old.id)
            locks = {(l.employee_id, l.day) for l in s.scalars(select(m.ScheduleLock).where(m.ScheduleLock.month_id==old.month_id))}
            allowed = set(month_days(data.year, data.month))
            for edit in getattr(payload, 'edits', []):
                if (edit.employee_id, edit.day) not in codes:
                    raise ValueError('Ô ngoài lịch.')
                codes[edit.employee_id, edit.day] = edit.code
            from .validation import shift_start
            for key in codes:
                if codes[key] != current.get(key) and (frozen_cell(data, key, current, now(), locks, allowed) or shift_start(data, key[1], codes[key])<=now()):
                    raise Conflict('Không sửa ô khóa hoặc ca/ngày đã bắt đầu.')
            profile = require(s, m.RuleProfile, month.rule_profile_id)
            report = validate_month(data, codes, prefs, profile.work_policy)
            self._check_neighbors(s, data, codes)
            snap = snapshot(s, month.id, context={'request_ids': []})
            from validator.quality import score_vector
            v = save_version(s, month.id, snap, codes, report.to_dict(), source='RESTORE' if restore else 'MANUAL',
                             parent=old.id, scores=score_vector(report))
            audit(s, 'DRAFT_CHILD', v.id, {'parent': old.id})
            return dict(revision=revision(s), version_id=v.id)

    def enqueue(self, payload):
        from .repository import application_sources
        with self.sessions.begin() as s:
            if revision(s) != payload.revision:
                raise Conflict('Dữ liệu đã đổi; tải lại trước khi chạy Solver.')
            if s.scalar(select(m.SolverRun.id).where(m.SolverRun.status.in_(['QUEUED','RUNNING','CANCELLING']))):
                raise Conflict('Đang có job Solver trên workspace; đợi hoặc hủy job trước.')
            if payload.mode=='ANNUAL':
                plan = require(s, m.AnnualPlan, payload.annual_plan_id)
                if plan.baseline_frozen:
                    raise Conflict('V0 năm đã khóa. Tạo kế hoạch mới hoặc dùng REPAIR từng tháng.')
                months = s.scalars(select(m.PlanningMonth).where(m.PlanningMonth.annual_plan_id==plan.id).order_by(m.PlanningMonth.year, m.PlanningMonth.month)).all()
                if not months or len(months)>12:
                    raise ValueError('Kế hoạch cần 1..12 tháng.')
                pairs = [(x.year, x.month) for x in months]
                if any((a[0]*12+a[1]+1)!=(b[0]*12+b[1]) for a, b in zip(pairs,pairs[1:])):
                    raise ValueError('Các tháng ANNUAL phải liên tiếp.')
                if any(not x.input_confirmed and not x.assumptions.strip() for x in months):
                    raise ValueError('Dữ liệu tương lai phải xác nhận hoặc có giả định rõ ràng.')
            else:
                months = [require(s, m.PlanningMonth, payload.month_id)]
            # FRESH is the explicit choice to rebuild the whole month using all
            # approved inputs. RESUME remains a quality-only continuation; new
            # requests on a confirmed schedule should normally use REPAIR.
            if payload.mode == 'RESUME' and months[0].current_version_id:
                days=month_days(months[0].year,months[0].month)
                if s.scalar(select(m.Request.id).where(m.Request.day.between(days[0],days[-1]),m.Request.status=='APPROVED').limit(1)):
                    raise Conflict('Lịch nền đã có yêu cầu mới chưa áp dụng. Dùng REPAIR để bảo toàn ổn định của lịch.')
            baseline = payload.baseline_version_id or (months[0].current_version_id if payload.mode in ('RESUME','REPAIR') else None)
            if payload.mode in ('RESUME','REPAIR') and baseline is None:
                raise ValueError('Chế độ này cần một phiên bản tham chiếu đầy đủ mã.')
            if baseline and require(s, m.ScheduleVersion, baseline).month_id != months[0].id:
                raise ValueError('Lịch tham chiếu không thuộc tháng.')
            selected = [require(s, m.Request, rid) for rid in set(payload.request_ids)]
            if payload.mode=='REPAIR':
                if payload.as_of is None:
                    raise ValueError('REPAIR cần as_of có múi giờ.')
                if any(r.day.year != months[0].year or r.day.month != months[0].month for r in selected):
                    raise Unsupported('REPAIR yêu cầu xuyên tháng chưa hỗ trợ mô hình liên kết. Không chạy và không lưu một phần.')
                if any(r.status not in ('APPROVED','APPLIED','CANCELLED') for r in selected):
                    raise ValueError('Chỉ REPAIR yêu cầu đã duyệt/áp dụng hoặc đã hủy rõ ràng.')
                pending = s.scalars(select(m.Request.id).where(m.Request.day.between(date(months[0].year, months[0].month, 1), month_days(months[0].year, months[0].month)[-1]), m.Request.status=='APPROVED')).all()
                if not set(pending)<=set(payload.request_ids):
                    raise Conflict('Cần chọn tất cả yêu cầu đã duyệt chưa áp dụng trong tháng để không bỏ yêu cầu bắt buộc.')
            snapshots = []
            for month in months:
                locks = [dict(employee_id=l.employee_id, day=str(l.day)) for l in s.scalars(select(m.ScheduleLock).where(m.ScheduleLock.month_id==month.id))]
                days=month_days(month.year,month.month)
                applied_request_ids=payload.request_ids if payload.mode=='REPAIR' else list(s.scalars(select(m.Request.id).where(
                    m.Request.day.between(days[0],days[-1]),m.Request.status=='APPROVED')))
                context = dict(baseline=baseline if len(months)==1 else None, current_version=month.current_version_id,
                               request_ids=applied_request_ids, locks=locks, options=payload.model_dump(mode='json'))
                snap = snapshot(s, month.id, context=context)
                snapshots.append(snap.id)
            run = m.SolverRun(id=str(uuid4()), scope_key='workspace', month_id=payload.month_id,
                              annual_plan_id=payload.annual_plan_id, mode=payload.mode, status='QUEUED', revision=revision(s),
                              options={**payload.model_dump(mode='json'), 'baseline_version_id': baseline}, snapshots=snapshots)
            s.add(run)
            s.flush()
            audit(s, 'SOLVER_QUEUED', run.id, run.options)
            return row_dict(run)

    def jobs(self):
        with self.sessions() as s:
            return [row_dict(j) for j in s.scalars(select(m.SolverRun).order_by(m.SolverRun.created_at.desc()).limit(100))]

    def job(self, job_id):
        with self.sessions() as s:
            return row_dict(require(s, m.SolverRun, job_id))
