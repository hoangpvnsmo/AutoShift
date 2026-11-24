"""E05: chỉ đếm chuyển loại ca giữa hai ngày ghi lịch liên tiếp, SHIFT."""
from dataclasses import dataclass
from datetime import timedelta
from core.validation.transition_checks import transition_config, gap_minutes

@dataclass(frozen=True)
class RotationRow:
    id: int
    changes: int
    short: int
    adjacent: int


def preferred_rest_minutes(data):
    h=data.rules.get('optimization',{}).get('preferred_transition_rest_hours',12)
    if type(h) is not int or not 0<=h<=48:
        raise ValueError('optimization.preferred_transition_rest_hours: số nguyên 0..48.')
    return h*60


def rotation_rows(data,days,assignments):
    days=tuple(days); cfg=transition_config(data); threshold=preferred_rest_minutes(data)
    rows=[]
    for e in data.employees:
        if e.group!='SHIFT': continue
        raw=data.previous[e.id,days[0]-timedelta(days=1)]
        prev=int(raw) if raw in ('1','2','3') else 0
        change=short=adjacent=0
        for d in days:
            cur=assignments[e.id,d]
            if prev and cur:
                adjacent+=1
                if prev!=cur:
                    change+=1
                    short+=int(gap_minutes(cfg,prev,cur)<threshold)
            prev=cur
        rows.append(RotationRow(e.id,change,short,adjacent))
    return tuple(rows)


def rotation_score(rows):
    v=[r.changes for r in rows]
    return (sum(v),sum(r.short for r in rows),max(v,default=0),max(v,default=0)-min(v,default=0))
