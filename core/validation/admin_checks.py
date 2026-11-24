"""Giai đoạn 3: chính sách ADMIN và kiểm tra độc lập với CP-SAT."""
from dataclasses import dataclass
from collections import Counter
from core.validation.credit_checks import request_map, check_work_credit, target_credit, SHIFT_CODES, NON_SHIFT_CODES
from core.data.calendar_utils import month_days

@dataclass(frozen=True)
class AdminPolicy:
    enabled: bool
    allowed_shifts: frozenset
    allow_holiday: bool
    max_shifts: int | None


def admin_policy(data):
    raw = data.rules['admin_support']
    for key in ('enabled', 'allow_weekend', 'allow_holiday'):
        if type(raw.get(key)) is not bool:
            raise ValueError(f'admin_support.{key}: cần true/false.')
    if raw['allow_weekend']:
        raise ValueError('D04: ADMIN không đi ca T7/CN; allow_weekend phải false.')
    shifts = raw.get('allowed_shifts')
    if not isinstance(shifts,list) or any(type(s) is not int or s not in (2,3) for s in shifts):
        raise ValueError('ADMIN chỉ được phép ca 2, 3.')
    cap = raw.get('max_shifts_per_person')
    if cap is not None and (type(cap) is not int or cap < 0):
        raise ValueError('max_shifts_per_person: cần null hoặc số nguyên không âm.')
    return AdminPolicy(raw['enabled'],frozenset(shifts),raw['allow_holiday'],cap)


def admin_allowed(e,d,s,data,policy):
    return (policy.enabled and e.group=='ADMIN' and s in policy.allowed_shifts
            and s in e.allowed_shifts and d.weekday()<5
            and (d not in data.holidays or policy.allow_holiday))


def validate_admin_requests(data):
    policy=admin_policy(data)
    req=request_map(data)
    people={e.id:e for e in data.employees}
    fixed=Counter()
    for (i,d),code in req.items():
        e=people[i]
        if e.group=='ADMIN' and code in SHIFT_CODES:
            if not admin_allowed(e,d,int(code),data,policy):
                raise ValueError(f'ID {i}, {d}: ca ADMIN cố định trái chính sách huy động.')
            fixed[i]+=1
    if policy.max_shifts is not None and any(n>policy.max_shifts for n in fixed.values()):
        raise ValueError('ADMIN: số ca cố định vượt giới hạn hỗ trợ tháng.')
    return policy,req

@dataclass(frozen=True)
class AdminCreditRow:
    id: int
    group: str
    target: int
    night: int
    morning: int
    afternoon: int
    ct: int
    dt: int
    bkd: int
    t: int
    off: int
    holiday_shifts: int

    @property
    def shifts(self): return self.night+self.morning+self.afternoon
    @property
    def total(self): return self.shifts+self.ct+self.dt+self.t
    @property
    def excess(self): return max(0,self.total-self.target)
    @property
    def missing(self): return max(0,self.target-self.total)


def check_admin_credits(data,days,assignments):
    """Khôi phục mã T/OFF và mã cố định; kiểm tra C/D độc lập bằng lịch kết quả."""
    check_work_credit(data)
    policy,req=validate_admin_requests(data)
    if tuple(days)!=month_days(data.year,data.month):
        raise ValueError('Cần đủ toàn bộ ngày tháng để kiểm tra công.')
    errors,codes,rows=[],{},[]
    for e in data.employees:
        counts=Counter(); holiday_shifts=0
        for d in days:
            key=e.id,d
            s=assignments[key]
            if type(s) is not int or s not in (0,1,2,3):
                raise ValueError(f'Assignment không hợp lệ: {key}: {s!r}')
            fixed=req.get(key)
            if fixed in SHIFT_CODES and s!=int(fixed):
                errors.append(f'C08: {key}: sai ca cố định {fixed}.')
            if fixed in NON_SHIFT_CODES and s:
                errors.append(f'C06/C07/C09: {key}: trùng ca và {fixed}.')
            if s and s not in e.allowed_shifts:
                errors.append(f'A06: {key}: sai quyền đi ca.')
            if s and e.group=='ADMIN' and not admin_allowed(e,d,s,data,policy):
                errors.append(f'D01/D03/D04/D05: {key}: ca ADMIN không được phép.')
            if s:
                c=str(s)
                holiday_shifts+=int(d in data.holidays)
            elif fixed in NON_SHIFT_CODES:
                c=fixed
            elif e.group=='ADMIN' and d.weekday()<5 and d not in data.holidays:
                c='T'
            else:
                c='OFF'
            counts[c]+=1; codes[key]=c
        row=AdminCreditRow(e.id,e.group,target_credit(data,e),counts['1'],counts['2'],counts['3'],counts['CT'],counts['DT'],counts['BKD'],counts['T'],counts['OFF'],holiday_shifts)
        if e.group=='ADMIN' and policy.max_shifts is not None and row.shifts>policy.max_shifts:
            errors.append(f'D06: ID {e.id}: {row.shifts} ca vượt giới hạn {policy.max_shifts}.')
        rows.append(row)
    return errors,codes,rows
