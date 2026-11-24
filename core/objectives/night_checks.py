"""E02: đánh giá số ca đêm độc lập với CP-SAT."""
from dataclasses import dataclass
from core.validation.credit_checks import target_credit


def night_groups(data):
    """So sánh SHIFT cùng quyền đi ca và công chuẩn; nhóm một người vẫn được báo cáo."""
    groups = {}
    for e in data.employees:
        allowed = tuple(sorted(set(e.allowed_shifts)))
        if e.group == 'SHIFT' and 1 in allowed:
            groups.setdefault((allowed, target_credit(data,e)), []).append(e.id)
    return {k: tuple(sorted(ids)) for k,ids in sorted(groups.items())}


@dataclass(frozen=True)
class NightGroupRow:
    allowed: tuple
    target: int
    counts: tuple  # ((id, number_of_nights), ...)

    @property
    def gap(self):
        n = [v for _,v in self.counts]
        return max(n)-min(n) if n else 0


def night_rows(data, days, assignments):
    return [NightGroupRow(allowed,target,tuple((i,sum(assignments[i,d]==1 for d in days)) for i in ids))
            for (allowed,target),ids in night_groups(data).items()]


def night_score(rows):
    gaps = [r.gap for r in rows]
    return max(gaps,default=0),sum(gaps)


def night_lower_bound(data, demand):
    """Cận dưới cho mô hình build_stage3: ADMIN không đi đêm, nhu cầu chính xác.
    Nếu chỉ một nhóm đủ điều kiện, tổng ca đêm của nhóm là hằng số.
    Nhiều nhóm: cận (0,0) an toàn; không giả định tổng riêng từng nhóm.
    """
    groups = night_groups(data)
    if len(groups)==1:
        n=len(next(iter(groups.values())))
        total=sum(v for (_,s),v in demand.items() if s==1)
        lower=int(total % n != 0)
        return lower,lower
    return 0,0
