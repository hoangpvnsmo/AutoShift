"""Portable snapshots and full-code schedules; no persistence or HTTP dependencies."""
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from core.data.models import Employee, MonthData, Request
from core.data.calendar_utils import month_days
from core.objectives.preference_checks import Preference

CODES = ('1', '2', '3', 'OFF', 'CT', 'DT', 'BKD', 'T')
CODE_ID = {c: j + 1 for j, c in enumerate(CODES)}
LOCAL_TZ = timezone(timedelta(hours=7))


class Conflict(ValueError):
    """An explicit revision or business conflict, never silently overwritten."""


class BoundaryConflict(Conflict):
    """A candidate violates an existing neighboring month, not an input revision."""
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details=details


class Unsupported(ValueError):
    """A case that must fail closed until implemented correctly."""


def now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)


def digest(value) -> str:
    return sha256(canonical(value).encode('utf-8')).hexdigest()


def serialize(data: MonthData, prefs=()) -> dict:
    return dict(year=data.year, month=data.month, holidays=sorted(map(str, data.holidays)),
                employees=[asdict(e) for e in data.employees],
                requests=[dict(employee_id=r.employee_id, day=str(r.day), code=r.code, note=r.note) for r in data.requests],
                previous=[[i, str(d), c] for (i, d), c in sorted(data.previous.items())],
                rules=data.rules,
                preferences=[dict(employee_id=p.employee_id, day=str(p.day), kind=p.kind, shift=p.shift, weight=p.weight) for p in prefs])


def deserialize(raw: dict) -> tuple[MonthData, tuple[Preference, ...]]:
    data = MonthData(raw['year'], raw['month'], frozenset(map(date.fromisoformat, raw['holidays'])),
                     tuple(Employee(**{**e, 'allowed_shifts': tuple(e['allowed_shifts'])}) for e in raw['employees']),
                     tuple(Request(**{**r, 'day': date.fromisoformat(r['day'])}) for r in raw['requests']),
                     {(i, date.fromisoformat(d)): c for i, d, c in raw['previous']}, raw['rules'])
    return data, tuple(Preference(**{**p, 'day': date.fromisoformat(p['day'])}) for p in raw.get('preferences', []))


def history_length(data: MonthData) -> int:
    from core.validation.transition_checks import transition_config
    return max(6, data.rules['max_consecutive_night_shifts'], data.rules['max_consecutive_shift_days'], transition_config(data).lookback)


def history_gaps(data: MonthData) -> list[tuple[int, date]]:
    first = date(data.year, data.month, 1)
    return [(e.id, first-timedelta(days=k)) for e in data.employees for k in range(1, history_length(data)+1)
            if (e.id, first-timedelta(days=k)) not in data.previous]


def assert_grid(data: MonthData, codes: dict) -> None:
    expected = {(e.id, d) for e in data.employees for d in month_days(data.year, data.month)}
    if set(codes) != expected or any(type(c) is not str or c not in CODES for c in codes.values()):
        raise ValueError('Lịch phải có đúng một mã đầy đủ cho mọi nhân sự/ngày; không dùng 0 hoặc ô trống.')


def numeric(codes: dict) -> dict:
    return {k: int(v) if v in ('1', '2', '3') else 0 for k, v in codes.items()}


def changed_cells(before: dict, after: dict) -> list[dict]:
    if set(before) != set(after):
        raise ValueError('Hai phiên bản không cùng nhân sự/ngày.')
    return [dict(employee_id=i, day=str(d), before=before[i, d], after=after[i, d])
            for i, d in sorted(before) if before[i, d] != after[i, d]]
