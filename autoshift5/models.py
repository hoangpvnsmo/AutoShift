"""Normalized SQLAlchemy 2 schema. Assignments are rows, snapshots are inputs."""
from datetime import date, datetime
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from .domain import now


class Base(DeclarativeBase):
    pass


class State(Base):
    __tablename__ = 'input_state'
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(default=1)


class Employee(Base):
    __tablename__ = 'employees'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    group: Mapped[str] = mapped_column(String(8))
    is_leader: Mapped[bool] = mapped_column(Boolean, default=False)
    active_from: Mapped[date] = mapped_column(Date, default=date(1900, 1, 1))
    active_until: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint("id>0 AND length(name)>0 AND \"group\" IN ('SHIFT','ADMIN')"),
                      CheckConstraint('active_until IS NULL OR active_until>=active_from'))


class Permission(Base):
    __tablename__ = 'employee_permissions'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    shift: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint('employee_id', 'shift', 'valid_from'), CheckConstraint('shift IN (1,2,3)'),)


class WorkCode(Base):
    __tablename__ = 'work_codes'
    code: Mapped[str] = mapped_column(String(5), primary_key=True)
    credit: Mapped[int] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("code IN ('1','2','3','OFF','CT','DT','BKD','T')"),)


class ShiftType(Base):
    __tablename__ = 'shift_types'
    code: Mapped[str] = mapped_column(ForeignKey('work_codes.code'), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class RuleProfile(Base):
    __tablename__ = 'rule_profiles'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    revision: Mapped[int] = mapped_column(Integer)
    rules: Mapped[dict] = mapped_column(JSON)
    work_policy: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('name', 'revision'), CheckConstraint('revision>0'),)


class AnnualPlan(Base):
    __tablename__ = 'annual_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    year: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='DRAFT')
    assumptions: Mapped[str] = mapped_column(Text, default='')
    baseline_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (CheckConstraint('year BETWEEN 1900 AND 2200'),)


class PlanningMonth(Base):
    __tablename__ = 'planning_months'
    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    annual_plan_id: Mapped[int | None] = mapped_column(ForeignKey('annual_plans.id'), index=True)
    rule_profile_id: Mapped[int] = mapped_column(ForeignKey('rule_profiles.id'))
    current_version_id: Mapped[str | None] = mapped_column(ForeignKey('schedule_versions.id', use_alter=True))
    baseline_version_id: Mapped[str | None] = mapped_column(ForeignKey('schedule_versions.id', use_alter=True))
    input_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    assumptions: Mapped[str] = mapped_column(Text, default='')
    __table_args__ = (UniqueConstraint('year', 'month'), CheckConstraint('month BETWEEN 1 AND 12'), CheckConstraint('year BETWEEN 1900 AND 2200'))


class MonthlySetting(Base):
    __tablename__ = 'employee_monthly_settings'
    month_id: Mapped[int] = mapped_column(ForeignKey('planning_months.id'), primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    target_credit: Mapped[int | None] = mapped_column(Integer)
    # Month-specific roles preserve historical rights when the employee master changes.
    name: Mapped[str] = mapped_column(String(200))
    group: Mapped[str] = mapped_column(String(8))
    is_leader: Mapped[bool] = mapped_column(Boolean)
    __table_args__ = (CheckConstraint('target_credit IS NULL OR target_credit>=0'),)


class Holiday(Base):
    __tablename__ = 'holidays'
    month_id: Mapped[int] = mapped_column(ForeignKey('planning_months.id'), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    note: Mapped[str] = mapped_column(Text, default='')


class Demand(Base):
    __tablename__ = 'staffing_demands'
    month_id: Mapped[int] = mapped_column(ForeignKey('planning_months.id'), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    shift: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    __table_args__ = (CheckConstraint('shift IN (1,2,3) AND count>=0'),)


class HistoricalAssignment(Base):
    __tablename__ = 'historical_assignments'
    month_id: Mapped[int] = mapped_column(ForeignKey('planning_months.id'), primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    code: Mapped[str] = mapped_column(ForeignKey('work_codes.code'))


class Request(Base):
    __tablename__ = 'requests'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    code: Mapped[str] = mapped_column(ForeignKey('work_codes.code'))
    reason: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(12))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','SUBMITTED','APPROVED','APPLIED','REJECTED','CANCELLED')"),
        Index('uq_request_active_cell', 'employee_id', 'day', unique=True,
              sqlite_where=text("status NOT IN ('REJECTED','CANCELLED')")),)


class RequestRevision(Base):
    __tablename__ = 'request_revisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey('requests.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('request_id', 'revision'),)


class Preference(Base):
    __tablename__ = 'preferences'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(20))
    shift: Mapped[int | None] = mapped_column(Integer)
    weight: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint('employee_id', 'day'), CheckConstraint('weight BETWEEN 1 AND 10'),
        CheckConstraint("(kind IN ('PREFER_SHIFT','AVOID_SHIFT') AND shift IN (1,2,3)) OR (kind IN ('PREFER_OFF','PREFER_WORK') AND shift IS NULL)"))


class InputSnapshot(Base):
    __tablename__ = 'input_snapshots'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    month_id: Mapped[int] = mapped_column(ForeignKey('planning_months.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    source_hashes: Mapped[dict] = mapped_column(JSON)
    work_policy: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ScheduleVersion(Base):
    __tablename__ = 'schedule_versions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    month_id: Mapped[int] = mapped_column(ForeignKey('planning_months.id'), index=True)
    parent_version_id: Mapped[str | None] = mapped_column(ForeignKey('schedule_versions.id'))
    annual_baseline_id: Mapped[str | None] = mapped_column(ForeignKey('schedule_versions.id'))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey('input_snapshots.id'))
    status: Mapped[str] = mapped_column(String(12), default='DRAFT')
    source: Mapped[str] = mapped_column(String(30))
    proof: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_status: Mapped[str] = mapped_column(String(20))
    input_revision: Mapped[int] = mapped_column(Integer)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (CheckConstraint("status IN ('DRAFT','PLANNED','PUBLISHED','ACTUAL')"),)


class Assignment(Base):
    __tablename__ = 'schedule_assignments'
    version_id: Mapped[str] = mapped_column(ForeignKey('schedule_versions.id'), primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    code: Mapped[str] = mapped_column(ForeignKey('work_codes.code'))
    __table_args__ = (CheckConstraint("code IN ('1','2','3','OFF','CT','DT','BKD','T')"), Index('ix_assignment_date_employee', 'day', 'employee_id'))


class ScheduleLock(Base):
    __tablename__ = 'schedule_locks'
    month_id: Mapped[int] = mapped_column(ForeignKey('planning_months.id'), primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    reason: Mapped[str] = mapped_column(Text, default='')


class ScheduleChange(Base):
    __tablename__ = 'schedule_changes'
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey('schedule_versions.id'), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    day: Mapped[date] = mapped_column(Date)
    before: Mapped[str] = mapped_column(ForeignKey('work_codes.code'))
    after: Mapped[str] = mapped_column(ForeignKey('work_codes.code'))


class SolverRun(Base):
    __tablename__ = 'solver_runs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(50))
    month_id: Mapped[int | None] = mapped_column(ForeignKey('planning_months.id'))
    annual_plan_id: Mapped[int | None] = mapped_column(ForeignKey('annual_plans.id'))
    mode: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(20), default='QUEUED')
    revision: Mapped[int] = mapped_column(Integer)
    options: Mapped[dict] = mapped_column(JSON)
    snapshots: Mapped[list] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    log: Mapped[str] = mapped_column(Text, default='')
    stage: Mapped[str] = mapped_column(String(150), default='Chờ worker')
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index('uq_active_job_scope', 'scope_key', unique=True,
                         sqlite_where=text("status IN ('QUEUED','RUNNING','CANCELLING')")),)


class ObjectiveScore(Base):
    __tablename__ = 'objective_scores'
    version_id: Mapped[str] = mapped_column(ForeignKey('schedule_versions.id'), primary_key=True)
    layer: Mapped[str] = mapped_column(String(30), primary_key=True)
    value: Mapped[list] = mapped_column(JSON)


class ValidationResult(Base):
    __tablename__ = 'validation_results'
    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey('schedule_versions.id'), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ImportRecord(Base):
    __tablename__ = 'import_records'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    month_id: Mapped[int] = mapped_column(ForeignKey('planning_months.id'))
    report: Mapped[dict] = mapped_column(JSON)
