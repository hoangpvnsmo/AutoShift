"""E04: OFF theo mã lịch; cuối tuần theo giờ ca. Không coi CT/DT/BKD là OFF."""
from dataclasses import dataclass
from datetime import timedelta
from core.validation.credit_checks import request_map
from core.validation.transition_checks import transition_config, DAY

ONE=timedelta(days=1)

@dataclass(frozen=True)
class RestWindows:
    singles: tuple
    long_windows: tuple
    blocks: tuple
    weekends: tuple
    pending_weekends: tuple

@dataclass(frozen=True)
class RestRow:
    id: int
    off: int
    singles: int
    long_extra: int
    blocks: int
    full_weekends: int


def rest_windows(data,days):
    days=tuple(days); current=set(days); first,last=days[0],days[-1]
    ids=[e.id for e in data.employees if e.group=='SHIFT']
    known=current|{d for d in (first-j*ONE for j in range(1,5))
                   if all((i,d) in data.previous for i in ids)}
    singles=tuple((d-ONE,d,d+ONE) for d in days if {d-ONE,d+ONE}<=known)
    long=tuple(tuple(d-j*ONE for j in (3,2,1,0)) for d in days
               if all(d-j*ONE in known for j in (3,2,1)))
    blocks=tuple((d-2*ONE,d-ONE,d) for d in days if {d-2*ONE,d-ONE}<=known)
    intervals=transition_config(data).intervals
    weekends=[]; pending=[]
    # Kỳ cuối tuần tính vào tháng chứa Chủ nhật, nếu có đủ dữ liệu giờ ca.
    for sun in (d for d in days if d.weekday()==6):
        sat=sun-ONE
        pairs=[]
        for delta in range(-1,3):
            d=sat+delta*ONE
            for s,(start,end) in intervals.items():
                if delta*DAY+start<2*DAY and delta*DAY+end>0:
                    pairs.append((d,s))
        required={sat,sun}|{d for d,s in pairs}
        if not required<=known:
            pending.append((sat,sun)); continue
        weekends.append((sat,sun,tuple(pairs)))
    # Thứ Bảy cuối tháng, Chủ nhật thuộc tháng sau: chưa đánh giá.
    if last.weekday()==5: pending.append((last,last+ONE))
    return RestWindows(singles,long,blocks,tuple(weekends),tuple(pending))


def rest_rows(data,days,assignments,windows=None):
    days=tuple(days); current=set(days); req=request_map(data)
    w=windows or rest_windows(data,days); out=[]
    for e in data.employees:
        if e.group!='SHIFT': continue
        i=e.id
        def code(d):
            if d not in current: return data.previous[i,d]
            s=assignments[i,d]
            return str(s) if s else req.get((i,d),'OFF')
        def off(d): return code(d)=='OFF'
        out.append(RestRow(i,sum(off(d) for d in days),
            sum(not off(a) and off(b) and not off(c) for a,b,c in w.singles),
            sum(all(off(d) for d in ds) for ds in w.long_windows),
            sum(not off(a) and off(b) and off(c) for a,b,c in w.blocks),
            sum(off(sat) and off(sun) and all(code(d)!=str(s) for d,s in pairs)
                for sat,sun,pairs in w.weekends)))
    return tuple(out)


def rest_scores(rows,weekend_count):
    def gap(values): return max(values,default=0)-min(values,default=0)
    a=(sum(r.singles for r in rows),max((r.singles for r in rows),default=0),sum(r.long_extra for r in rows))
    b=(sum(r.full_weekends==0 for r in rows) if weekend_count else 0,
       len(rows)*weekend_count-sum(r.full_weekends for r in rows),
       gap([r.full_weekends for r in rows]),gap([r.blocks for r in rows]))
    return a,b


def weekend_lower(data,windows,demand,admin_support):
    """Cận an toàn: cuối tuần OFF không thể chứa bất kỳ ca theo nhãn ngày T7/CN.
    ADMIN cấm T7/CN; nếu dữ liệu lịch sử thì không suy diễn nhu cầu tháng trước.
    admin_support không cần dùng vì D04 vẫn cấm cuối tuần kể cả bật lễ.
    """
    n=sum(e.group=='SHIFT' for e in data.employees)
    missing=sum(max(sum(v for (d,s),v in demand.items() if d==day)
                    for day in (sat,sun)) for sat,sun,_ in windows.weekends)
    total=n*len(windows.weekends)
    # Ít nhất n - số cuối tuần-người tối đa sẽ không có cuối tuần trọn vẹn.
    return (max(0,n-(total-missing)) if windows.weekends else 0,missing,0,0)
