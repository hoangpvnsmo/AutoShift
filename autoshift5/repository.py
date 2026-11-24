"""Database loader and append-only schedule repository."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from core.data.models import MonthData, Employee as DomainEmployee, Request as DomainRequest
from core.data.calendar_utils import month_days
from core.objectives.preference_checks import Preference as DomainPreference, validate_preferences
from core.validation.credit_checks import demand_requirements
from validator.hard_rules import config_result
from validator.result import Status
from . import models as m
from .domain import Conflict, assert_grid, serialize, digest, changed_cells
from .db import ROOT, revision


def require(s: Session, model, key):
    obj = s.get(model, key)
    if obj is None:
        raise ValueError(f'Không tìm thấy {model.__tablename__}: {key}')
    return obj


def load_month(s: Session, month_id: int) -> tuple[MonthData, tuple]:
    month = require(s, m.PlanningMonth, month_id)
    first, *_, last = month_days(month.year, month.month)
    settings = s.scalars(select(m.MonthlySetting).where(m.MonthlySetting.month_id == month_id).order_by(m.MonthlySetting.position)).all()
    perms = s.scalars(select(m.Permission).where(m.Permission.valid_from <= last,
                        (m.Permission.valid_until.is_(None)) | (m.Permission.valid_until >= first))).all()
    people = []
    for e in settings:
        ep = [p for p in perms if p.employee_id == e.employee_id]
        if any(p.valid_from > first or (p.valid_until and p.valid_until < last) for p in ep):
            raise ValueError('Core tháng chưa hỗ trợ thay quyền ca giữa tháng; chia kỳ hoặc dùng cấu hình tháng đầy đủ.')
        allowed = tuple(sorted({p.shift for p in ep}))
        people.append(DomainEmployee(e.employee_id, e.name, e.group, e.is_leader, allowed, e.target_credit))
    ids = {e.id for e in people}
    req = tuple(DomainRequest(r.employee_id, r.day, r.code, r.reason) for r in s.scalars(
        select(m.Request).where(m.Request.day.between(first, last), m.Request.employee_id.in_(ids),
                                m.Request.status.in_(['APPROVED', 'APPLIED'])).order_by(m.Request.employee_id, m.Request.day)))
    holidays = frozenset(s.scalars(select(m.Holiday.day).where(m.Holiday.month_id == month_id)))
    previous = {(p.employee_id, p.day): p.code for p in s.scalars(select(m.HistoricalAssignment).where(m.HistoricalAssignment.month_id == month_id))}
    rules = deepcopy(require(s, m.RuleProfile, month.rule_profile_id).rules)
    data = MonthData(month.year, month.month, holidays, tuple(people), req, previous, rules)
    expected = demand_requirements(data)
    for v in s.scalars(select(m.Demand).where(m.Demand.month_id == month_id)):
        if expected.get((v.day, v.shift)) != v.count:
            rules.setdefault('credit_policy', {}).setdefault('daily_demand', {}).setdefault(str(v.day), {})[str(v.shift)] = v.count
    prefs = tuple(DomainPreference(p.employee_id, p.day, p.kind, p.shift, p.weight) for p in s.scalars(
        select(m.Preference).where(m.Preference.day.between(first, last), m.Preference.employee_id.in_(ids)).order_by(m.Preference.employee_id, m.Preference.day)))
    result = config_result(data, prefs)
    if result.status != Status.PASS:
        raise ValueError('; '.join(f.message for f in result.findings))
    # The nearest confirmed month wins over stale imported history. Snapshots
    # already saved remain immutable and are never loaded through this path.
    from datetime import timedelta
    from .domain import history_length
    previous_date=first-timedelta(days=1)
    prior_month=s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==previous_date.year,
                                                      m.PlanningMonth.month==previous_date.month))
    if prior_month and prior_month.current_version_id:
        confirmed=codes_for(s,prior_month.current_version_id)
        for e in data.employees:
            for k in range(1,history_length(data)+1):
                key=e.id,first-timedelta(days=k)
                if key in confirmed:previous[key]=confirmed[key]
    return data, validate_preferences(data, prefs)


def codes_for(s: Session, version_id: str) -> dict:
    require(s, m.ScheduleVersion, version_id)
    return {(a.employee_id, a.day): a.code for a in s.scalars(select(m.Assignment).where(m.Assignment.version_id == version_id))}


def application_sources() -> dict:
    import hashlib
    sources={p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for folder in ('core', 'validator', 'autoshift5') for p in sorted((ROOT/folder).rglob('*.py'))}
    sources['_runtime_sha256']=digest(runtime_versions())
    return sources


def runtime_versions():
    from importlib.metadata import version
    import sys
    return {'python':sys.version,'platform':sys.platform,**{package:version(package) for package in
            ('ortools','protobuf','openpyxl','sqlalchemy','pydantic')}}


def snapshot(s, month_id, *, data=None, prefs=None, context=None):
    if data is None:
        data, prefs = load_month(s, month_id)
    raw = serialize(data, prefs or ())
    raw['_context'] = context or {}
    raw['_runtime_versions']=runtime_versions()
    profile = require(s, m.RuleProfile, require(s, m.PlanningMonth, month_id).rule_profile_id)
    snap = m.InputSnapshot(id=str(uuid4()), month_id=month_id, revision=revision(s),
        digest=digest(raw), payload=raw, source_hashes=application_sources(), work_policy=profile.work_policy)
    s.add(snap)
    s.flush()
    return snap


def save_version(s, month_id, snap, codes, validation, *, source, parent=None, proof=None, scores=None,
                 repair=None, status='DRAFT'):
    from .domain import deserialize
    data, _ = deserialize(snap.payload)
    assert_grid(data, codes)
    if validation['errors']:
        raise ValueError('Validator từ chối lịch có lỗi cứng.')
    month = require(s, m.PlanningMonth, month_id)
    v = m.ScheduleVersion(id=str(uuid4()), month_id=month_id, parent_version_id=parent,
          annual_baseline_id=month.baseline_version_id, snapshot_id=snap.id, status='DRAFT', source=source,
          proof=proof or {'status': 'NOT_PROVEN'}, validation_status=validation['verdict'], input_revision=snap.revision)
    s.add(v)
    s.flush()
    s.add_all(m.Assignment(version_id=v.id, employee_id=i, day=d, code=c) for (i, d), c in codes.items())
    s.add(m.ValidationResult(version_id=v.id, payload={**validation, 'repair': repair}))
    if scores:
        s.add_all(m.ObjectiveScore(version_id=v.id, layer=str(j), value=list(row)) for j, row in enumerate(scores))
    if repair is not None:
        s.add(m.ObjectiveScore(version_id=v.id, layer='REPAIR', value=repair['vector']))
    if parent:
        s.add_all(m.ScheduleChange(version_id=v.id, employee_id=c['employee_id'], day=date.fromisoformat(c['day']),
                                  before=c['before'], after=c['after']) for c in changed_cells(codes_for(s, parent), codes))
    s.flush()
    if status != 'DRAFT':
        v.status = status
        s.flush()
    return v


def validation_for(s, version_id):
    return s.scalar(select(m.ValidationResult).where(m.ValidationResult.version_id == version_id).order_by(m.ValidationResult.id.desc())).payload
