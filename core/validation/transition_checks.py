"""B02/B04/B05: giờ ca theo ngày ghi lịch; kiểm tra nghỉ giữa các ca trực."""
from datetime import datetime, time, timedelta
from dataclasses import dataclass
import re
from core.validation.sequence_checks import history_codes, SHIFT_CODES, sequence_limits

DAY = 1440

@dataclass(frozen=True)
class TransitionConfig:
    intervals: dict
    min_minutes: int
    explicit: frozenset
    lookback: int


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError(f'Giờ ca không hợp lệ {value!r}; dùng HH:MM (00:00-23:59).')
    h, m = map(int, value.split(':'))
    return h * 60 + m


def transition_config(data):
    raw = data.rules.get('shift_hours')
    if not isinstance(raw, dict) or set(raw) != {'1', '2', '3'}:
        raise ValueError('Cần khai báo shift_hours cho đủ ca 1, 2, 3 trong config/rules.json. Xem README_Buoc2_5.md.')
    intervals = {}
    for s in (1, 2, 3):
        v = raw[str(s)]
        if not isinstance(v, dict) or not {'start', 'end', 'start_day_offset'} <= v.keys():
            raise ValueError(f'Ca {s}: cần start, end, start_day_offset.')
        offset = v['start_day_offset']
        if type(offset) is not int or offset not in (-1, 0):
            raise ValueError(f'Ca {s}: start_day_offset phải là -1 hoặc 0.')
        start, end = _clock(v['start']), _clock(v['end'])
        if end == start:
            raise ValueError(f'Ca {s}: giờ bắt đầu và kết thúc không được trùng.')
        if end < start:
            end += DAY
        intervals[s] = (start + DAY * offset, end + DAY * offset)
    # Bảo đảm ca được gán theo đúng thứ tự thời gian trong một chu kỳ ngày.
    a, b, c = (intervals[s][0] for s in (1,2,3))
    if not a < b < c < a + DAY:
        raise ValueError('Giờ ca phải theo thứ tự 1, 2, 3 trong một chu kỳ 24 giờ; kiểm tra start_day_offset.')
    hours = data.rules.get('min_rest_hours')
    if type(hours) is not int or not 0 <= hours <= 24:
        raise ValueError('min_rest_hours: V1 hỗ trợ số nguyên từ 0 đến 24.')
    pairs = data.rules.get('forbidden_transitions')
    if not isinstance(pairs, list):
        raise ValueError('forbidden_transitions phải là danh sách các cặp ca.')
    explicit = set()
    for pair in pairs:
        if not isinstance(pair, (tuple,list)) or len(pair) != 2 or any(type(s) is not int or s not in (1,2,3) for s in pair):
            raise ValueError(f'Cặp chuyển ca không hợp lệ: {pair!r}.')
        explicit.add(tuple(pair))
    if (3,1) not in explicit:
        raise ValueError('B02 bắt buộc phải có [3, 1] trong forbidden_transitions.')
    # Xét mọi khoảng cách ngày có khả năng thiếu nghỉ, kể cả qua ngày OFF.
    threshold = hours*60 + max(e for _,e in intervals.values()) - min(s for s,_ in intervals.values())
    lookback = max(1, (threshold-1)//DAY)
    if lookback > max(sequence_limits(data)):
        raise ValueError('Lịch sử hiện tại không đủ để kiểm tra thời gian nghỉ đã cấu hình.')
    return TransitionConfig(intervals, hours*60, frozenset(explicit), lookback)


def gap_minutes(cfg, before, after, day_gap=1):
    return day_gap*DAY + cfg.intervals[after][0] - cfg.intervals[before][1]


def forbidden_pairs(cfg, day_gap=1):
    return {(a,b) for a in (1,2,3) for b in (1,2,3)
            if gap_minutes(cfg,a,b,day_gap) < cfg.min_minutes
            or (day_gap == 1 and (a,b) in cfg.explicit)}


def check_transitions(data, days, assignments):
    """Kiểm tra độc lập bằng datetime trên từng ca thực tế, không dùng ma trận cấm.
    Trả (violations, statistics): statistics[id] = số phút nghỉ ngắn nhất
    giữa các ca có ca sau nằm trong tháng; None nếu không đủ hai ca.
    Không kiểm tra giờ làm CT/DT/T/BKD vì chưa có dữ liệu giờ của các trạng thái đó.
    """
    cfg = transition_config(data)
    days = tuple(days)
    errors, stats = [], {}
    for e in data.employees:
        history = history_codes(data,e.id,days[0],cfg.lookback)
        records = []
        for k,code in enumerate(history):
            if code in SHIFT_CODES:
                records.append((days[0]-timedelta(days=len(history)-k),int(code)))
        for d in days:
            s = assignments[e.id,d]
            if type(s) is not int or s not in (0,1,2,3):
                raise ValueError(f'Assignment không hợp lệ: ID {e.id}, {d}, {s!r}.')
            if s:
                records.append((d,s))
        shortest = None
        for (d1,s1),(d2,s2) in zip(records,records[1:]):
            if d2 < days[0]:
                continue
            # Tính datetime thực, tách biệt với cách mã hóa pair trong CP-SAT.
            end = datetime.combine(d1,time()) + timedelta(minutes=cfg.intervals[s1][1])
            start = datetime.combine(d2,time()) + timedelta(minutes=cfg.intervals[s2][0])
            gap = int((start-end).total_seconds()//60)
            shortest = gap if shortest is None else min(shortest,gap)
            desc = f'ID {e.id}: {d1} ca {s1} -> {d2} ca {s2}'
            if (d2-d1).days == 1 and (s1,s2) in cfg.explicit:
                errors.append(f'B02: {desc}, tổ hợp bị cấm.')
            if gap < cfg.min_minutes:
                errors.append(f'B04: {desc}, nghỉ {gap/60:g}h < {cfg.min_minutes/60:g}h.')
        stats[e.id] = shortest
    return errors, stats
