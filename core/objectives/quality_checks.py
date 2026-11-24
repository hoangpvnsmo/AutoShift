"""Thống kê chất lượng lịch độc lập với solver (bước 4.1)."""
from dataclasses import dataclass
from collections import Counter

@dataclass(frozen=True)
class ShiftBalanceRow:
    id: int
    counts: tuple[int, int, int]
    allowed: tuple[int, ...]

    @property
    def gap(self):
        values = [self.counts[s-1] for s in self.allowed]
        return max(values)-min(values) if values else 0


def shift_balance_rows(data, days, assignments):
    rows = []
    for e in data.employees:
        if e.group != 'SHIFT':
            continue
        counts = Counter(assignments[e.id, d] for d in days)
        rows.append(ShiftBalanceRow(e.id, tuple(counts[s] for s in (1,2,3)), tuple(sorted(set(e.allowed_shifts)))))
    return rows


def shift_balance_score(rows):
    gaps = [r.gap for r in rows]
    return max(gaps, default=0), sum(gaps)


def workforce_score(rows):
    """Tính lại 5 mục tiêu stage 3 theo đúng thứ tự; không dùng số float."""
    shift = [r for r in rows if r.group == 'SHIFT']
    admin = [r for r in rows if r.group == 'ADMIN']
    totals = [r.total for r in shift]
    supports = [r.shifts for r in admin]
    if not totals:
        raise ValueError('Cần ít nhất một nhân sự SHIFT.')
    n, a = len(totals), len(supports)
    total, support = sum(totals), sum(supports)
    return (sum(r.excess for r in shift), support, max(totals)-min(totals),
            sum(abs(n*t-total) for t in totals), sum(abs(a*s-support) for s in supports))
