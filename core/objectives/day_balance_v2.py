"""E03 v2: cận dưới theo A07, độ lệch trung bình nguyên, không phụ thuộc solver."""
from dataclasses import dataclass
from datetime import timedelta
from core.validation.admin_checks import admin_policy, admin_allowed
from core.objectives.day_balance_checks import day_balance_rows


def distribution_score(values):
    values=list(values); n=len(values); total=sum(values)
    if not n: return (0,0)
    return max(values)-min(values), sum(abs(n*v-total) for v in values)


def day_score_v2(rows, kind):
    if kind not in ('holiday','weekend'): raise ValueError('Loại ngày không hợp lệ.')
    return distribution_score(getattr(r,kind) for r in rows if r.group=='SHIFT')


@dataclass(frozen=True)
class DayBound:
    lower: tuple
    total_min: int
    total_max: int
    support_max: int
    n: int
    overlap: int

    @property
    def fixed(self): return self.total_min==self.total_max

    @property
    def ideal_values(self):
        """Một phân bổ đủ để đạt cận; chỉ dùng với tổng cố định."""
        if not self.fixed: return None
        t=self.total_max; u=min(self.support_max,t)
        if not u: return (0,)*self.n
        q,r=divmod(t,u)
        return (0,)*(self.n-u)+(q,)*(u-r)+(q+1,)*r


def day_bound(data, dates, demand, admin_total):
    """Chỉ dùng trên mô hình build_stage3, A07, tối đa 1 ca/ngày.
    Nếu ADMIN không thể tham gia tập ngày, tổng SHIFT cố định. Với các cặp
    ngày liên tiếp, số lượt lặp >= số ca * min_previous_day_staff. Tổng các
    giao kề nhau <= tổng lượt - số người khác nhau (đếm theo từng người).
    Cận độ lệch: chia đều t lượt trên tối đa u người; phần còn lại bằng 0.
    Nếu ADMIN có thể làm thay đổi tổng, bỏ qua overlap, lấy min từng cận
    trên khoảng tổng khả dĩ; không lấy tổng của nghiệm hiện tại làm hằng số.
    """
    dates=set(dates); n=sum(e.group=='SHIFT' for e in data.employees)
    t=sum(v for (d,s),v in demand.items() if d in dates)
    policy=admin_policy(data)
    cap=sum(any(admin_allowed(e,d,s,data,policy) for s in (1,2,3))
            for e in data.employees if e.group=='ADMIN' for d in dates)
    lo=max(0,t-min(cap,admin_total)); overlap=0
    if lo==t and n and t:
        k=data.rules.get('min_previous_day_staff',0)
        for d in dates:
            if d-timedelta(days=1) in dates:
                overlap+=k*sum((d,s) in demand for s in (1,2,3))
    u=min(n,max(0,t-overlap)) if lo==t else n
    if not n: return DayBound((0,0),lo,t,0,n,overlap)
    if lo==t:
        if t and not u: raise ValueError('Cận A07 không nhất quán với lịch nền.')
        # Chia đều trên u vị trí có thể khác 0; thêm n-u số 0.
        q,r=divmod(t,u) if u else (0,0)
        values=[0]*(n-u)+[q]*(u-r)+[q+1]*r
        lower=distribution_score(values)
    else:
        # Tổng biến thiên; các cận riêng lẻ an toàn ngay cả khi không cùng đạt.
        lower=(min(int(v%n!=0) for v in range(lo,t+1)),
               min(2*(v%n)*(n-v%n) for v in range(lo,t+1)))
    return DayBound(lower,lo,t,u,n,overlap)


def add_day_balance_v2(ctx,data,dates,kind):
    from core.objectives.day_balance_constraints import add_day_balance
    q=add_day_balance(ctx,data,dates,kind); m=ctx.model
    n=len(q.counts); D=len(dates); total=m.new_int_var(0,n*D,f'E03_{kind}_total')
    m.add(total==sum(q.counts.values()))
    deviations=[]
    for i,v in q.counts.items():
        a=m.new_int_var(0,n*D,f'E03_{kind}_dev_{i}')
        m.add_abs_equality(a,n*v-total); deviations.append(a)
    bound=n*n*D
    dev=m.new_int_var(0,bound,f'E03_{kind}_total_dev')
    m.add(dev==sum(deviations))
    return q,(q.gap,dev),(bound+1)*q.gap+dev
