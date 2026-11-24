"""Các ràng buộc bắt buộc, bước 2.2 (A02, A03, A04, A06)."""
from core.data.models import MonthData
from core.model.model_builder import ScheduleModel


def _int(value, label, minimum):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{label}: cần số nguyên >= {minimum}.')
    return value


def add_staffing_constraints(ctx: ScheduleModel, data: MonthData) -> None:
    """A02/A04: giới hạn số người; A03: đủ điều độ viên phụ trách."""
    leaders = [e.id for e in data.employees if e.is_leader]
    min_leaders = _int(data.rules['min_leaders_per_shift'], 'min_leaders_per_shift', 1)
    limits = {}
    for s in ctx.shifts:
        cfg = data.rules['staff_per_shift'][str(s)]
        low = _int(cfg['min'], f'Ca {s}: min', 3)
        high = _int(cfg['max'], f'Ca {s}: max', low)
        preferred = _int(cfg['preferred'], f'Ca {s}: preferred', low)
        if preferred > high or min_leaders > high:
            raise ValueError(f'Ca {s}: cấu hình số người/phụ trách không hợp lệ.')
        limits[s] = (low, high)
    for d in ctx.days:
        for s in ctx.shifts:
            low, high = limits[s]
            total = sum(ctx.x[i, d, s] for i in ctx.employee_ids)
            ctx.model.add(total >= low)  # A02
            ctx.model.add(total <= high)  # A04
            # A03: phụ trách là một phần của tổng số người, không phải người bổ sung.
            ctx.model.add(sum(ctx.x[i, d, s] for i in leaders) >= min_leaders)


def add_shift_eligibility(ctx: ScheduleModel, data: MonthData) -> None:
    """A06: quyền đi từng loại ca lấy từ Employees."""
    for e in data.employees:
        for s in ctx.shifts:
            if s not in e.allowed_shifts:
                for d in ctx.days:
                    ctx.model.add(ctx.x[e.id, d, s] == 0)


def disable_admin_for_trial(ctx: ScheduleModel, data: MonthData) -> None:
    """Khóa hành chính chỉ cho thử nghiệm 2.2; không phải D01-D10 hoàn chỉnh."""
    for e in data.employees:
        if e.group == 'ADMIN':
            for d in ctx.days:
                ctx.model.add(ctx.worked[e.id, d] == 0)


def add_previous_day_continuity(ctx: ScheduleModel, data: MonthData) -> None:
    """A07: mỗi ca có đủ người đã trực bất kỳ ca nào ngày trước.

    Ngày đầu tháng dùng lịch thực tế trong Previous; các ngày còn lại
    dùng biến worked của mô hình. CT, DT, BKD, T, OFF không phải ca trực.
    """
    from datetime import timedelta

    minimum = _int(data.rules['min_previous_day_staff'],
                   'min_previous_day_staff', 1)
    for s in ctx.shifts:
        if minimum > data.rules['staff_per_shift'][str(s)]['max']:
            raise ValueError(f'Ca {s}: min_previous_day_staff vượt số người tối đa.')

    first = ctx.days[0]
    previous_day = first - timedelta(days=1)
    previous_staff = []
    for i in ctx.employee_ids:
        if (i, previous_day) not in data.previous:
            raise ValueError(f'Previous: thiếu ID {i}, ngày {previous_day}.')
        code = data.previous[i, previous_day]
        if code not in {'1', '2', '3', 'CT', 'DT', 'BKD', 'T', 'OFF'}:
            raise ValueError(f'Previous: mã không hợp lệ của ID {i}, ngày {previous_day}: {code!r}.')
        if code in {'1', '2', '3'}:
            previous_staff.append(i)

    # Ngày 1: lịch D-1 đã biết, không cần biến phụ.
    for s in ctx.shifts:
        ctx.model.add(sum(ctx.x[i, first, s] for i in previous_staff) >= minimum)

    # Ngày 2 trở đi: y = x AND worked ngày trước.
    for d in ctx.days[1:]:
        prev = d - timedelta(days=1)
        for s in ctx.shifts:
            carryover = []
            for i in ctx.employee_ids:
                x = ctx.x[i, d, s]
                w = ctx.worked[i, prev]
                y = ctx.model.new_bool_var(f'carryover_{i}_{d:%Y%m%d}_{s}')
                ctx.model.add(y <= x)
                ctx.model.add(y <= w)
                ctx.model.add(y >= x + w - 1)
                carryover.append(y)
            ctx.model.add(sum(carryover) >= minimum)


def add_consecutive_shift_constraints(ctx: ScheduleModel, data: MonthData) -> None:
    """B01/B03/B05: cửa sổ trượt, kể cả qua ranh giới tháng."""
    from core.validation.sequence_checks import sequence_limits, history_codes, SHIFT_CODES

    max_night, max_work = sequence_limits(data)
    history_size = max(max_night, max_work)
    # Đọc đủ lịch sử trước khi thêm ràng buộc để tránh mặc định ngày thiếu là nghỉ.
    history = {i: history_codes(data, i, ctx.days[0], history_size)
               for i in ctx.employee_ids}
    for i in ctx.employee_ids:
        night = [int(c == '1') for c in history[i][-max_night:]]
        night += [ctx.x[i, d, 1] for d in ctx.days]
        work = [int(c in SHIFT_CODES) for c in history[i][-max_work:]]
        work += [ctx.worked[i, d] for d in ctx.days]

        # Mỗi cửa sổ có max+1 ngày; cửa sổ đầu tiên kết thúc vào ngày 1.
        for seq, limit in ((night, max_night), (work, max_work)):
            for start in range(len(ctx.days)):
                ctx.model.add(sum(seq[start:start + limit + 1]) <= limit)


def add_shift_transition_constraints(ctx: ScheduleModel, data: MonthData) -> None:
    """B02/B04/B05: cấm cặp ca theo giờ nghỉ và quy định chuyển ca."""
    from datetime import timedelta
    from core.validation.transition_checks import transition_config, forbidden_pairs
    from core.validation.sequence_checks import history_codes, SHIFT_CODES

    cfg = transition_config(data)
    first = ctx.days[0]
    history = {i: dict(zip((first-timedelta(days=k)
                            for k in range(cfg.lookback,0,-1)),
                           history_codes(data,i,first,cfg.lookback)))
               for i in ctx.employee_ids}
    pairs = {k: forbidden_pairs(cfg,k) for k in range(1,cfg.lookback+1)}
    for i in ctx.employee_ids:
        for d in ctx.days:
            for k, forbidden in pairs.items():
                prev = d-timedelta(days=k)
                if prev < first:
                    code = history[i][prev]
                    if code in SHIFT_CODES:
                        for a,b in forbidden:
                            if a == int(code):
                                ctx.model.add(ctx.x[i,d,b] == 0)
                else:
                    for a,b in forbidden:
                        ctx.model.add(ctx.x[i,prev,a] + ctx.x[i,d,b] <= 1)
