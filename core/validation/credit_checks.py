"""Bước 2.6: dữ liệu nhu cầu, yêu cầu cá nhân và kiểm tra công độc lập solver."""
from dataclasses import dataclass
from datetime import date, timedelta
from core.data.calendar_utils import month_days, standard_credit

SHIFT_CODES = {'1', '2', '3'}
NON_SHIFT_CODES = {'OFF', 'CT', 'DT', 'BKD'}

@dataclass(frozen=True)
class CreditRow:
    id: int
    target: int
    night: int
    morning: int
    afternoon: int
    ct: int
    dt: int
    bkd: int
    off: int

    @property
    def total(self):
        return self.night + self.morning + self.afternoon + self.ct + self.dt

    @property
    def excess(self):
        return max(0, self.total - self.target)

    @property
    def missing(self):
        return max(0, self.target - self.total)


def demand_requirements(data):
    """Nhu cầu chính xác theo loại ca; mặc định min, không tự dùng preferred cũ."""
    policy = data.rules.get('credit_policy', {})
    if policy.get('mode', 'DEMAND_BASED') != 'DEMAND_BASED':
        raise ValueError('Bước 2.6 chỉ hỗ trợ DEMAND_BASED.')
    override = policy.get('demand_staff_per_shift', {})
    if not isinstance(override, dict) or set(override) - {'1', '2', '3'}:
        raise ValueError('demand_staff_per_shift: chỉ chấp nhận khóa 1, 2, 3.')
    result = {}
    for s in (1, 2, 3):
        cfg = data.rules['staff_per_shift'][str(s)]
        n = override.get(str(s), cfg['min'])
        if type(n) is not int or not cfg['min'] <= n <= cfg['max']:
            raise ValueError(f'Nhu cầu ca {s} phải là số nguyên trong [min, max].')
        result[s] = n
    # Các ngày đặc biệt có thể có nhu cầu riêng, không bắt buộc khai báo.
    daily = policy.get('daily_demand', {})
    if not isinstance(daily, dict):
        raise ValueError('daily_demand phải là object theo ngày YYYY-MM-DD.')
    days = month_days(data.year, data.month)
    requirements = {(d,s): n for d in days for s,n in result.items()}
    for raw, shifts in daily.items():
        d = date.fromisoformat(raw)
        if d not in days or not isinstance(shifts,dict) or set(shifts)-{'1','2','3'}:
            raise ValueError(f'daily_demand: ngày/ca không hợp lệ: {raw}.')
        for key,n in shifts.items():
            cfg = data.rules['staff_per_shift'][key]
            if type(n) is not int or not cfg['min'] <= n <= cfg['max']:
                raise ValueError(f'Nhu cầu {d}, ca {key} ngoài [min, max].')
            requirements[d,int(key)] = n
    return requirements


def request_map(data):
    """Chỉ yêu cầu cứng ở V1; giữ nguyên mã ĐT chuẩn hóa thành DT."""
    ids = {e.id:e for e in data.employees}
    days = set(month_days(data.year,data.month))
    req = {}
    for r in data.requests:
        key = (r.employee_id,r.day)
        if key in req or r.employee_id not in ids or r.day not in days:
            raise ValueError(f'Requests: ID/ngày không hợp lệ hoặc trùng: {key}.')
        c = r.code
        if c not in SHIFT_CODES | NON_SHIFT_CODES:
            raise ValueError(f'Requests: mã {c!r} không hỗ trợ ở bước 2.6.')
        if c in SHIFT_CODES and int(c) not in ids[r.employee_id].allowed_shifts:
            raise ValueError(f'ID {r.employee_id}, {r.day}: ca cố định không được phép.')
        req[key] = c
    return req


def target_credit(data,e):
    return standard_credit(data) if e.target_credit is None else e.target_credit


def check_work_credit(data):
    expected = {'1':1,'2':1,'3':1,'CT':1,'DT':1,'BKD':0,'OFF':0,'T':1}
    raw = data.rules['work_credit']
    if any(type(raw.get(k)) is not int or raw[k] != v for k,v in expected.items()):
        raise ValueError('work_credit khác bộ quy đổi V1 đã thống nhất.')


def check_credits(data, days, assignments):
    """Kiểm tra Requests và tính công bằng đếm mã, không dùng biến CP-SAT.
    Chỉ thống kê SHIFT; hành chính tạm khóa, chưa xếp T ở bước này.
    Trả (errors, codes, rows). codes chứa cả trạng thái phi ca đã khai báo.
    """
    check_work_credit(data)
    req = request_map(data)
    errors, codes, rows = [], {}, []
    for e in data.employees:
        counts = {c:0 for c in SHIFT_CODES | NON_SHIFT_CODES}
        for d in days:
            key = (e.id,d)
            s = assignments[key]
            if type(s) is not int or s not in (0,1,2,3):
                raise ValueError(f'Assignment không hợp lệ: ID {e.id}, {d}: {s!r}.')
            expected = req.get(key)
            if expected in SHIFT_CODES and s != int(expected):
                errors.append(f'C08: ID {e.id}, {d}: phải trực ca {expected}, thực tế {s}.')
            if expected in NON_SHIFT_CODES and s:
                errors.append(f'C06/C07/C09: ID {e.id}, {d}: {expected} trùng ca {s}.')
            if e.group == 'ADMIN' and s:
                errors.append(f'ID {e.id}, {d}: hành chính đang khóa ở bước 2.6.')
            if s and s not in e.allowed_shifts:
                errors.append(f'A06: ID {e.id}, {d}: ca {s} không được phép.')
            c = str(s) if s else (expected if expected in NON_SHIFT_CODES else 'OFF')
            # ADMIN không có mã trạng thái hoàn chỉnh nếu chưa xếp công T.
            if e.group == 'SHIFT' or expected is not None or s:
                codes[key] = c
            counts[c] += 1
        if e.group == 'SHIFT':
            rows.append(CreditRow(e.id,target_credit(data,e),counts['1'],counts['2'],counts['3'],
                                  counts['CT'],counts['DT'],counts['BKD'],counts['OFF']))
    return errors,codes,rows


def check_staffing(data, days, assignments):
    """Kiểm tra độc lập A01-A07 và nhu cầu chính xác."""
    demands = demand_requirements(data)
    leaders = {e.id for e in data.employees if e.is_leader}
    people = {e.id:e for e in data.employees}
    previous = days[0]-timedelta(days=1)
    prev = {i for i in people if data.previous[i,previous] in SHIFT_CODES}
    errors = []
    for d in days:
        current = set()
        for s in (1,2,3):
            ids = {i for i in people if assignments[i,d] == s}
            if len(ids) != demands[d,s]:
                errors.append(f'Nhu cầu: {d}, ca {s}: {len(ids)} != {demands[d,s]}.')
            if len(ids & leaders) < data.rules['min_leaders_per_shift']:
                errors.append(f'A03: {d}, ca {s}: thiếu phụ trách.')
            if len(ids & prev) < data.rules['min_previous_day_staff']:
                errors.append(f'A07: {d}, ca {s}: thiếu người nối tiếp.')
            if current & ids or any(s not in people[i].allowed_shifts for i in ids):
                errors.append(f'A05/A06: {d}, ca {s}: trùng ca hoặc sai quyền.')
            current.update(ids)
        prev = current
    return errors
