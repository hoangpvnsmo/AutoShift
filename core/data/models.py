from dataclasses import dataclass
from datetime import date

@dataclass(frozen=True)
class Employee:
    id: int
    name: str
    group: str
    is_leader: bool
    allowed_shifts: tuple[int, ...]
    target_credit: int | None = None

@dataclass(frozen=True)
class Request:
    employee_id: int
    day: date
    code: str
    note: str = ""

@dataclass(frozen=True)
class MonthData:
    year: int
    month: int
    holidays: frozenset[date]
    employees: tuple[Employee, ...]
    requests: tuple[Request, ...]
    previous: dict[tuple[int, date], str]
    rules: dict
