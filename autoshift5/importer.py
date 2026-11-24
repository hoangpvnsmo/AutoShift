"""Strict, transactional, idempotent import. Never writes original files."""
from dataclasses import asdict
from datetime import date
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from core.data.data_loader import load_data
from core.data.calendar_utils import month_days
from core.objectives.preference_checks import load_preferences
from core.validation.credit_checks import demand_requirements
from validator.excel_schedule import read_schedule
from validator.quality import validate_quality, score_vector
from . import models as m
from .domain import serialize, history_gaps, canonical, Conflict
from .db import bump, audit
from .repository import load_month, snapshot, save_version

HEADERS = {'Employees': ['id', 'name', 'group', 'is_leader', 'shift_1', 'shift_2', 'shift_3', 'target_credit'],
           'Month': ['year', 'month'], 'Holidays': ['date', 'note'], 'Requests': ['id', 'date', 'code', 'note'],
           'Previous': ['id', 'date', 'code'], 'Preferences': ['employee_id', 'date', 'preference', 'shift', 'weight']}


def inspect_workbook(path):
    wb = load_workbook(path, read_only=True, data_only=False)
    source = {}
    try:
        for ws in wb:
            raw = list(ws.values)
            if ws.title not in {*HEADERS, 'Guide'}:
                raise ValueError(f'Sheet chưa nhận diện: {ws.title}; không bỏ qua dữ liệu.')
            if ws.title in HEADERS:
                n = len(HEADERS[ws.title])
                if tuple(raw[0][:n]) != tuple(HEADERS[ws.title]) or any(any(c is not None for c in row[n:]) for row in raw):
                    raise ValueError(f'{ws.title}: cột không nhận diện được.')
                for row in ws:
                    if any(c.data_type == 'f' for c in row):
                        raise ValueError(f'{ws.title}: công thức đầu vào không được hỗ trợ; cần giá trị rõ ràng.')
            source[ws.title] = [[str(v) if hasattr(v, 'isoformat') else v for v in row] for row in raw]
        return source
    finally:
        wb.close()


def import_legacy(engine, excel, rules, schedule=None, *, dry_run=True) -> dict:
    excel, rules = Path(excel).resolve(), Path(rules).resolve()
    source = inspect_workbook(excel)
    data = load_data(excel, rules)
    prefs = load_preferences(excel, data)
    raw = serialize(data, prefs)
    files = [excel, rules] + ([Path(schedule).resolve()] if schedule else [])
    hashes = {str(p): sha256(p.read_bytes()).hexdigest() for p in files}
    import_hash = sha256(canonical(hashes).encode('utf-8')).hexdigest()
    report = dict(dry_run=dry_run, month=f'{data.year:04d}-{data.month:02d}', employees=len(data.employees),
                  requests=len(data.requests), preferences=len(prefs), history_cells=len(data.previous),
                  missing_extended_history=len(history_gaps(data)), source_files=hashes, source_rows=source,
                  warnings=['Giờ CT/DT/T chưa được khai báo; không đủ điều kiện xác nhận hoàn toàn.'],
                  checkpoint_proofs_imported=False, field_equivalence=None)
    if history_gaps(data):
        report['warnings'].append('Previous đủ cho hard rules cũ nhưng chưa đủ toàn bộ cửa sổ E05 (cần tối đa 6 ngày).')
    codes = None
    validation = None
    if schedule:
        cells = read_schedule(schedule, data)
        validation = validate_quality(data, cells, prefs=prefs, source='legacy_excel')
        if validation.errors:
            raise ValueError('Lịch Excel không hợp lệ: ' + '; '.join(f.message for f in validation.errors[:8]))
        codes = {(i, d): c for i, d, c in cells}
        report['schedule_cells'] = len(codes)
    Session = sessionmaker(engine)
    with Session() as s:
        prior = s.get(m.ImportRecord, import_hash)
        if prior:
            return {**prior.report, 'dry_run': dry_run, 'already_imported': True, 'month_id': prior.month_id}
        try:
            if s.scalar(select(m.PlanningMonth.id).where(m.PlanningMonth.year == data.year, m.PlanningMonth.month == data.month)):
                raise Conflict('Tháng này đã có dữ liệu khác; không ghi đè bằng import. Dùng quản lý revision.')
            profile = m.RuleProfile(name=f'Import {data.year}-{data.month}-{import_hash[:8]}', revision=1, rules=data.rules)
            s.add(profile)
            s.flush()
            assumptions = '\n'.join(str(r) for r in source.get('Guide', []))
            plan = s.scalar(select(m.AnnualPlan).where(m.AnnualPlan.year == data.year).order_by(m.AnnualPlan.id))
            if plan is None:
                plan = m.AnnualPlan(name=f'Kế hoạch {data.year}', year=data.year, assumptions=assumptions)
                s.add(plan)
                s.flush()
            month = m.PlanningMonth(year=data.year, month=data.month, rule_profile_id=profile.id, annual_plan_id=plan.id,
                                    assumptions=assumptions, input_confirmed=False)
            s.add(month)
            s.flush()
            days = month_days(data.year, data.month)
            for pos, e in enumerate(data.employees):
                if s.get(m.Employee, e.id) is None:
                    s.add(m.Employee(id=e.id, name=e.name, group=e.group, is_leader=e.is_leader))
                    s.flush()
                s.add(m.MonthlySetting(month_id=month.id, employee_id=e.id, position=pos, target_credit=e.target_credit,
                                       name=e.name, group=e.group, is_leader=e.is_leader))
                s.add_all(m.Permission(employee_id=e.id, shift=k, valid_from=days[0], valid_until=days[-1]) for k in e.allowed_shifts)
            s.flush()
            notes = {str(row[0])[:10]: str(row[1] or '') for row in source['Holidays'][1:] if row[0]}
            s.add_all(m.Holiday(month_id=month.id, day=d, note=notes.get(str(d), '')) for d in data.holidays)
            s.add_all(m.Demand(month_id=month.id, day=d, shift=k, count=n) for (d, k), n in demand_requirements(data).items())
            s.add_all(m.HistoricalAssignment(month_id=month.id, employee_id=i, day=d, code=c) for (i, d), c in data.previous.items())
            for r in data.requests:
                rid = str(uuid4())
                s.add(m.Request(id=rid, employee_id=r.employee_id, day=r.day, code=r.code, reason=r.note,
                                status='APPLIED' if codes else 'APPROVED', revision=1))
                s.flush()
                s.add(m.RequestRevision(request_id=rid, revision=1, payload=dict(employee_id=r.employee_id, day=str(r.day), code=r.code,
                       reason=r.note, status='APPLIED' if codes else 'APPROVED')))
            s.add_all(m.Preference(employee_id=p.employee_id, day=p.day, kind=p.kind, shift=p.shift, weight=p.weight) for p in prefs)
            s.flush()
            loaded, loaded_prefs = load_month(s, month.id)
            normalized = serialize(loaded, loaded_prefs)
            # Ordering is not a business difference, but every field and note is compared.
            def ordered(v):
                v = dict(v)
                for name, keys in [('employees', ('id',)), ('requests', ('employee_id', 'day')), ('preferences', ('employee_id', 'day'))]:
                    v[name] = sorted(v[name], key=lambda r: tuple(r[k] for k in keys))
                return v
            diff = [k for k in raw if ordered(raw)[k] != ordered(normalized)[k]]
            if diff:
                raise ValueError('Excel Loader và Database Loader không tương đương: ' + ', '.join(diff))
            report['field_equivalence'] = {k: True for k in raw}
            report['month_id'] = month.id
            bump(s)
            if codes:
                snap = snapshot(s, month.id, context={'import': hashes, 'source_rows': source})
                version = save_version(s, month.id, snap, codes, validation.to_dict(), source='IMPORT_EXCEL', status='PLANNED',
                                        scores=score_vector(validation), proof={'status': 'NOT_IMPORTED', 'reason': 'Excel không chứng minh tối ưu'})
                month.current_version_id = version.id
                month.baseline_version_id = version.id
                report['version_id'] = version.id
            audit(s, 'IMPORT', month.id, {k: v for k, v in report.items() if k != 'source_rows'})
            s.add(m.ImportRecord(digest=import_hash, month_id=month.id, report=report))
            s.flush()
            if dry_run:
                s.rollback()
            else:
                s.commit()
            return report
        except Exception:
            s.rollback()
            raise
