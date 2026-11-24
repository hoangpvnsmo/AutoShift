"""Cận từ phân bổ E03 đã khóa. Cận là theo thứ tự từ điển, không phải từng thành phần độc lập."""
from core.objectives.rest_checks import weekend_lower


def min_touched(n,total,holiday_total,holiday_days):
    """DP: n người, nonholiday counts chỉ q/q+1; phân bổ thêm h_i trong [0,H].
    Tối thiểu tổng ceil((a_i+h_i)/2). Bỏ qua vị trí ngày và các rule khác => cận dưới.
    """
    if n<=0 or min(total,holiday_total,holiday_days)<0 or holiday_total>n*holiday_days:
        raise ValueError('Dữ liệu cận cuối tuần không hợp lệ.')
    q,r=divmod(total,n)
    a=[q]*(n-r)+[q+1]*r
    dp={0:0}
    for v in a:
        new={}
        for used,cost in dp.items():
            for h in range(min(holiday_days,holiday_total-used)+1):
                k=used+h; value=cost+(v+h+1)//2
                if k not in new or value<new[k]: new[k]=value
        dp=new
    return dp[holiday_total]


def weekend_lower_v2(data,days,windows,demand,admin_total,weekend_layer):
    base=weekend_lower(data,windows,demand,admin_total)
    n=sum(e.group=='SHIFT' for e in data.employees); W=len(windows.weekends)
    # Chỉ dùng phân bổ E03 khi chính xác cùng tập T7/CN của tháng, đủ dữ liệu.
    dates={d for sat,sun,_ in windows.weekends for d in (sat,sun)}
    all_weekend={d for d in days if d.weekday()>=5}
    if not n or not W or dates!=all_weekend or weekend_layer.status=='DISABLED': return base
    normal=dates-set(data.holidays); hol=dates & set(data.holidays)
    total=sum(v for (d,s),v in demand.items() if d in normal)
    holiday_total=sum(v for (d,s),v in demand.items() if d in hol)
    # D04 cấm ADMIN trực T7/CN (kể cả lễ). E03 gap <=1 => phân bố q/q+1.
    if data.rules.get('admin_support',{}).get('allow_weekend') is not False: return base
    if weekend_layer.after[0]>1: return base
    missing=max(base[1],min_touched(n,total,holiday_total,len(hol)))
    full=n*W-missing
    if full<0: raise ValueError('Cận E04b không nhất quán với dữ liệu.')
    # Gap >=1 CHỈ nếu đạt đúng cận missing. Đây là cận từ điển hợp lệ.
    return (max(0,n-full),missing,int(full%n!=0),0)
