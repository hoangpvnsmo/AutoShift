from dataclasses import dataclass
from datetime import date
from ortools.sat.python import cp_model
from core.data.models import MonthData
from core.data.calendar_utils import month_days


@dataclass
class ScheduleModel:
    model: cp_model.CpModel
    days: tuple[date, ...]
    employee_ids: tuple[int, ...]
    shifts: tuple[int, ...]
    x: dict[tuple[int, date, int], cp_model.IntVar]
    worked: dict[tuple[int, date], cp_model.IntVar]


def build_model(data: MonthData) -> ScheduleModel:
    model = cp_model.CpModel()
    days = month_days(data.year, data.month)
    employee_ids = tuple(e.id for e in data.employees)
    shifts = (1, 2, 3)
    if data.rules['max_shifts_per_day'] != 1:
        raise ValueError('V1.0 chỉ hỗ trợ tối đa 1 ca/ngày.')
    x = {}
    worked = {}
    for i in employee_ids:
        for d in days:
            for s in shifts:
                x[i, d, s] = model.new_bool_var(f'x_{i}_{d:%Y%m%d}_{s}')
            worked[i, d] = model.new_bool_var(f'worked_{i}_{d:%Y%m%d}')
            # A05: worked là Boolean, do đó tổng 3 biến ca chỉ có thể là 0 hoặc 1.
            model.add(worked[i, d] == sum(x[i, d, s] for s in shifts))
    return ScheduleModel(model, days, employee_ids, shifts, x, worked)
