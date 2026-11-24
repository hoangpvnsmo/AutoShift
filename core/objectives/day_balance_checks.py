"""E03: phân loại ngày theo ngày gán ca, đếm độc lập CP-SAT."""
from dataclasses import dataclass
from core.validation.admin_checks import admin_policy, admin_allowed
from core.validation.credit_checks import request_map


def day_sets(data, days):
    holiday = tuple(d for d in days if d in data.holidays)
    weekend = tuple(d for d in days if d.weekday() >= 5 and d not in data.holidays)
    return holiday, weekend


@dataclass(frozen=True)
class DayBalanceRow:
    id: int
    group: str
    holiday: int
    weekend: int


def day_balance_rows(data, days, assignments):
    holiday, weekend = day_sets(data, days)
    return [DayBalanceRow(e.id, e.group,
                          sum(assignments[e.id,d] in (1,2,3) for d in holiday),
                          sum(assignments[e.id,d] in (1,2,3) for d in weekend))
            for e in data.employees]


def day_score(rows, kind):
    if kind not in ('holiday','weekend'):
        raise ValueError('Loại ngày không hợp lệ.')
    values = [getattr(r,kind) for r in rows if r.group == 'SHIFT']
    return (max(values)-min(values) if values else 0,)


def day_lower_bound(data, dates, demand, admin_total):
    """Cận dưới an toàn: số lượt SHIFT nằm trong [D-cap, D].
    cap là cận trên lượt ADMIN trên các ngày xét, không phải số lượt dự báo.
    Nếu khoảng chứa bội của n thì chỉ chứng minh được cận 0.
    """
    dates=set(dates); n=sum(e.group=='SHIFT' for e in data.employees)
    if n <= 1: return (0,)
    total=sum(v for (d,s),v in demand.items() if d in dates)
    policy=admin_policy(data)
    cap=sum(any(admin_allowed(e,d,s,data,policy) for s in (1,2,3))
            for e in data.employees if e.group=='ADMIN' for d in dates)
    lo=max(0,total-min(cap,admin_total)); hi=total
    return (int(((lo+n-1)//n)*n > hi),)


def e01_lower_bound(data, days, demand, workforce):
    """Cận E01 từ tổng công và spread đã khóa, không phụ thuộc lịch nghiệm.
    Spread 0/1: mỗi SHIFT có q hoặc q+1 công. DP chọn ai nhận q+1
    để tối thiểu số người có số ca không chia hết cho số loại ca được phép.
    Bỏ qua ràng buộc ngày/ca, nên chỉ là cận dưới (không ép một lịch).
    Với spread >1, dùng cận 0 an toàn.
    """
    people=[e for e in data.employees if e.group=='SHIFT']; n=len(people)
    if not n or workforce[2]>1: return (0,0)
    req=request_map(data)
    fixed={e.id:sum(req.get((e.id,d)) in ('CT','DT') for d in days) for e in people}
    total=sum(demand.values())-workforce[1]+sum(fixed.values())
    q,r=divmod(total,n)
    dp={0:0}
    for e in people:
        new={}; k=len(set(e.allowed_shifts))
        for used,penalty in dp.items():
            for extra in (0,1):
                if used+extra>r: continue
                shifts=q+extra-fixed[e.id]
                if shifts<0 or (k==0 and shifts>0): continue
                cost=int(k>1 and shifts%k!=0)
                j=used+extra
                new[j]=min(new.get(j,n+1),penalty+cost)
        dp=new
    if r not in dp: raise ValueError('Cận E01: tổng công/spread không nhất quán với dữ liệu.')
    low=dp[r]
    return int(low>0),low
