"""Typed external contracts; business constraints live in application services."""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, AwareDatetime, model_validator

Code = Literal['1', '2', '3', 'OFF', 'CT', 'DT', 'BKD', 'T']
RequestStatus = Literal['DRAFT', 'SUBMITTED', 'APPROVED', 'APPLIED', 'REJECTED', 'CANCELLED']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Revision(StrictModel):
    revision: int = Field(ge=1)


class ImportInput(StrictModel):
    excel: str = 'input/input.xlsx'
    rules: str = 'config/rules.json'
    schedule: str | None = None
    dry_run: bool = True


class EmployeeInput(Revision):
    id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    group: Literal['SHIFT', 'ADMIN']
    is_leader: bool = False
    allowed_shifts: list[Literal[1, 2, 3]]
    month_ids: list[int] = Field(min_length=1)
    target_credit: int | None = Field(default=None, ge=0)


class RequestBatch(Revision):
    employee_ids: list[int] = Field(min_length=1, max_length=500)
    from_date: date
    to_date: date
    code: Code
    reason: str = Field(default='', max_length=2000)
    status: Literal['DRAFT', 'SUBMITTED', 'APPROVED'] = 'APPROVED'
    replace_ids: list[str] = []

    @model_validator(mode='after')
    def valid_range(self):
        if self.to_date < self.from_date or (self.to_date-self.from_date).days > 366:
            raise ValueError('Khoảng ngày không hợp lệ hoặc vượt 367 ngày.')
        return self


class RequestTransition(Revision):
    request_revision: int = Field(ge=1)
    status: Literal['SUBMITTED', 'APPROVED', 'REJECTED', 'CANCELLED']


class PreferenceInput(Revision):
    employee_id: int = Field(gt=0)
    day: date
    kind: Literal['PREFER_OFF', 'PREFER_SHIFT', 'AVOID_SHIFT', 'PREFER_WORK']
    shift: Literal[1, 2, 3] | None = None
    weight: int = Field(default=1, ge=1, le=10)


class JobInput(Revision):
    mode: Literal['FRESH', 'RESUME', 'REPAIR', 'ANNUAL', 'BALANCE']
    month_id: int | None = None
    annual_plan_id: int | None = None
    baseline_version_id: str | None = None
    request_ids: list[str] = []
    budget: float = Field(default=60, gt=0, le=86400)
    passes: int = Field(default=1, ge=1, le=20)
    repair_mode: Literal['FAST', 'OPTIMAL'] = 'FAST'
    as_of: AwareDatetime | None = None
    allowed_from: date | None = None
    allowed_until: date | None = None
    max_changes: int | None = Field(default=None, ge=0)
    no_admin_increase: bool = False
    no_overtime_increase: bool = False
    disabled_layers: list[Literal['R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8']] = []
    cascade: bool = False
    cascade_months: int = Field(default=12, ge=0, le=24)
    annual_balance: bool = True
    balance_from_month: int = Field(default=1,ge=1,le=12)
    balance_window_months: int = Field(default=3,ge=1,le=6)


class ConfirmInput(Revision):
    expected_current_version_id: str | None
    status: Literal['PLANNED', 'PUBLISHED', 'ACTUAL'] = 'PLANNED'


class YearClearInput(Revision):
    year: int = Field(ge=1900,le=2200)


class CellEdit(StrictModel):
    employee_id: int
    day: date
    code: Code


class DraftEdit(Revision):
    edits: list[CellEdit] = Field(min_length=1)


class LockInput(Revision):
    month_id: int
    employee_ids: list[int] = Field(min_length=1)
    day: date
    reason: str = ''
    locked: bool = True


class MonthConfig(Revision):
    max_consecutive_night_shifts: int = Field(ge=1, le=31)
    max_consecutive_shift_days: int = Field(ge=1, le=31)
    min_rest_hours: int = Field(ge=0, le=24)
    min_leaders_per_shift: int = Field(ge=1)
    min_previous_day_staff: int = Field(ge=1)
    balance_shift_types: bool
    balance_night_shifts: bool
    balance_holiday_shifts: bool
    balance_weekend_shifts: bool
    balance_rest_days: bool
    minimize_shift_changes: bool
    honor_preferences: bool


class StaffingInput(StrictModel):
    day: date
    shift: Literal[1,2,3]
    count: int = Field(ge=0)


class CalendarConfig(Revision):
    holidays: list[date]
    demands: list[StaffingInput]
    input_confirmed: bool = False
    assumptions: str = ''


class AnnualInput(Revision):
    name: str = Field(min_length=1)
    year: int = Field(ge=1900, le=2200)
    source_month_id: int
    months: list[int] = Field(min_length=1, max_length=12)
    assumptions: str = Field(min_length=1)


class WorkActivity(StrictModel):
    kind: Literal['work','rest']
    start: str | None = Field(default=None,pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end: str | None = Field(default=None,pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')


class WorkTimePolicy(StrictModel):
    min_rest_hours: float = Field(ge=0,le=24)
    codes: dict[Literal['CT','DT','T','BKD'],WorkActivity|None]


class WorkTimeInput(Revision):
    policy: WorkTimePolicy


class JobView(BaseModel):
    id: str
    mode: str
    status: str
    stage: str
    log: str
    result: dict | None
    error: str | None
    options: dict
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
