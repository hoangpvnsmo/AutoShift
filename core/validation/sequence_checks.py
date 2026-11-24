"""Dữ liệu lịch sử và bộ kiểm tra độc lập chuỗi ca B01/B03/B05."""
from datetime import timedelta

SHIFT_CODES = {'1', '2', '3'}
VALID_CODES = SHIFT_CODES | {'CT', 'DT', 'BKD', 'T', 'OFF'}


def sequence_limits(data):
    values = []
    for key in ('max_consecutive_night_shifts', 'max_consecutive_shift_days'):
        value = data.rules[key]
        if type(value) is not int or value < 1:
            raise ValueError(f'{key}: cần số nguyên >= 1.')
        values.append(value)
    return tuple(values)


def history_codes(data, employee_id, first_day, count):
    """Lấy đúng count ngày trước ngày đầu tháng, theo thứ tự thời gian."""
    result = []
    for n in range(count, 0, -1):
        d = first_day - timedelta(days=n)
        key = (employee_id, d)
        if key not in data.previous:
            raise ValueError(f'Previous: thiếu ID {employee_id}, ngày {d}.')
        code = data.previous[key]
        if code not in VALID_CODES:
            raise ValueError(f'Previous: mã không hợp lệ ID {employee_id}, ngày {d}: {code!r}.')
        result.append(code)
    return result


def check_sequences(data, days, assignments):
    """Kiểm tra bằng bộ đếm độc lập với mô hình; chỉ báo vi phạm ở tháng mới.

    assignments[(id, date)] là int 0/1/2/3 theo solver.py hiện tại.
    Trả về (violations, statistics). Chuỗi dài nhất có tính phần nối tháng trước.
    """
    max_night, max_work = sequence_limits(data)
    violations, statistics = [], {}
    for e in data.employees:
        night = work = 0
        for code in history_codes(data, e.id, days[0], max(max_night, max_work)):
            night = night + 1 if code == '1' else 0
            work = work + 1 if code in SHIFT_CODES else 0
        peak_night = peak_work = 0
        for d in days:
            s = assignments[e.id, d]
            if type(s) is not int or s not in (0, 1, 2, 3):
                raise ValueError(f'Assignment không hợp lệ ID {e.id}, ngày {d}: {s!r}.')
            night = night + 1 if s == 1 else 0
            work = work + 1 if s in (1, 2, 3) else 0
            peak_night, peak_work = max(peak_night, night), max(peak_work, work)
            if night > max_night:
                violations.append(f'B01: ID {e.id}, ngày {d}, ít nhất {night} ca đêm liên tiếp (max {max_night}).')
            if work > max_work:
                violations.append(f'B03: ID {e.id}, ngày {d}, ít nhất {work} ngày trực liên tiếp (max {max_work}).')
        statistics[e.id] = (peak_night, peak_work)
    return violations, statistics
