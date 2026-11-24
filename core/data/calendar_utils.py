import calendar
from datetime import date, timedelta
from core.data.models import MonthData, Employee


def month_days(year: int, month: int) -> tuple[date, ...]:
    return tuple(date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1]+1))


def previous_days(year: int, month: int, n: int = 5) -> tuple[date, ...]:
    first = date(year, month, 1)
    return tuple(first-timedelta(days=i) for i in range(n, 0, -1))


def standard_credit(data: MonthData) -> int:
    return sum(d.weekday() < 5 and d not in data.holidays for d in month_days(data.year, data.month))


def employee_credit(data: MonthData, emp: Employee) -> dict:
    req = [r for r in data.requests if r.employee_id == emp.id]
    ct = sum(r.code == 'CT' for r in req)
    dt = sum(r.code == 'DT' for r in req)
    target = standard_credit(data) if emp.target_credit is None else emp.target_credit
    unavailable = sum(r.code in {'OFF', 'CT', 'DT', 'BKD'} for r in req)
    fixed = sum(r.code in {'1', '2', '3'} for r in req)
    needed = target-ct-dt if emp.group == 'SHIFT' else None
    return {'target': target, 'CT': ct, 'DT': dt, 'BKD': sum(r.code=='BKD' for r in req),
            'OFF': sum(r.code=='OFF' for r in req), 'fixed_shifts': fixed,
            'available_days': len(month_days(data.year, data.month))-unavailable,
            'required_shifts': needed, 'remaining_shifts': None if needed is None else needed-fixed}
