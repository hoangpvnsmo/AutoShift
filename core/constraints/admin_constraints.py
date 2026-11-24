"""Giai đoạn 3: rule ADMIN, công và hàm mục tiêu phân cấp cho cả 21 người."""
from dataclasses import dataclass
from core.validation.admin_checks import validate_admin_requests, admin_allowed
from core.validation.credit_checks import check_work_credit, target_credit, SHIFT_CODES
from core.constraints.credit_constraints import add_demand_constraints

@dataclass
class WorkforceObjective:
    total: dict
    excess: dict
    missing: dict
    admin_t: dict
    admin_counts: dict
    shift_excess: object
    admin_total: object
    shift_spread: object
    shift_deviation: object
    admin_deviation: object
    objective: object


def add_admin_constraints(ctx,data):
    policy,req=validate_admin_requests(data)
    for e in data.employees:
        if e.group!='ADMIN': continue
        for d in ctx.days:
            for s in ctx.shifts:
                if not admin_allowed(e,d,s,data,policy):
                    ctx.model.add(ctx.x[e.id,d,s]==0)
        if policy.max_shifts is not None:
            ctx.model.add(sum(ctx.worked[e.id,d] for d in ctx.days)<=policy.max_shifts)
    return policy


def add_all_request_constraints(ctx,data):
    _,req=validate_admin_requests(data)
    for (i,d),c in req.items():
        if c in SHIFT_CODES:
            ctx.model.add(ctx.x[i,d,int(c)]==1)
        else:
            ctx.model.add(ctx.worked[i,d]==0)
    return req


def add_workforce_objective(ctx,data):
    """Thay add_credit_objective 2.6 vì tổng công SHIFT không còn cố định.
    Gọi sau các ràng buộc nhu cầu, ADMIN và Requests.
    """
    check_work_credit(data)
    _,req=validate_admin_requests(data)
    m=ctx.model; D=len(ctx.days)
    shift_ids=[e.id for e in data.employees if e.group=='SHIFT']
    admin_ids=[e.id for e in data.employees if e.group=='ADMIN']
    n,a=len(shift_ids),len(admin_ids)
    if not n: raise ValueError('Cần ít nhất một nhân sự SHIFT.')
    total,excess,missing,admin_t,admin_counts={},{},{},{},{}
    for e in data.employees:
        i=e.id; target=target_credit(data,e)
        fixed=sum(req.get((i,d)) in {'CT','DT'} for d in ctx.days)
        t_vars=[]
        if e.group=='ADMIN':
            for d in ctx.days:
                t=m.new_bool_var(f'admin_T_{i}_{d:%Y%m%d}')
                if d.weekday()<5 and d not in data.holidays and (i,d) not in req:
                    m.add(t+ctx.worked[i,d]==1)
                else:
                    m.add(t==0)
                admin_t[i,d]=t; t_vars.append(t)
            admin_counts[i]=m.new_int_var(0,D,f'admin_count_{i}')
            m.add(admin_counts[i]==sum(ctx.worked[i,d] for d in ctx.days))
        total[i]=m.new_int_var(0,D,f'credit_{i}')
        m.add(total[i]==sum(ctx.worked[i,d] for d in ctx.days)+fixed+sum(t_vars))
        excess[i]=m.new_int_var(0,D,f'excess_{i}')
        missing[i]=m.new_int_var(0,target,f'missing_{i}')
        m.add_max_equality(excess[i],[total[i]-target,0])
        m.add_max_equality(missing[i],[target-total[i],0])
    shift_excess=m.new_int_var(0,n*D,'shift_excess_sum')
    m.add(shift_excess==sum(excess[i] for i in shift_ids))
    admin_total=m.new_int_var(0,a*D,'admin_support_total')
    m.add(admin_total==sum(admin_counts.values()))
    hi=m.new_int_var(0,D,'shift_max_credit'); lo=m.new_int_var(0,D,'shift_min_credit')
    m.add_max_equality(hi,[total[i] for i in shift_ids]); m.add_min_equality(lo,[total[i] for i in shift_ids])
    spread=m.new_int_var(0,D,'shift_credit_spread'); m.add(spread==hi-lo)
    # Trung bình SHIFT thay đổi theo số lượt hỗ trợ. Dùng tổng công như biến.
    shift_sum=m.new_int_var(0,n*D,'shift_credit_sum'); m.add(shift_sum==sum(total[i] for i in shift_ids))
    devs=[]
    for i in shift_ids:
        v=m.new_int_var(0,n*D,f'shift_deviation_{i}')
        m.add_abs_equality(v,n*total[i]-shift_sum); devs.append(v)
    sd_bound=n*n*D
    sd=m.new_int_var(0,sd_bound,'shift_deviation_sum'); m.add(sd==sum(devs))
    # Công bằng ADMIN: tổng độ lệch quanh số ca hỗ trợ trung bình.
    ad_devs=[]
    for i in admin_ids:
        v=m.new_int_var(0,a*D,f'admin_deviation_{i}')
        m.add_abs_equality(v,a*admin_counts[i]-admin_total); ad_devs.append(v)
    ad_bound=a*a*D
    ad=m.new_int_var(0,ad_bound,'admin_deviation_sum'); m.add(ad==sum(ad_devs))
    # Lexicographic bằng trọng số dựa vào cận trên, không cần giải nhiều lần.
    terms=[(shift_excess,n*D),(admin_total,a*D),(spread,D),(sd,sd_bound),(ad,ad_bound)]
    weight=1; objective=0
    for var,bound in reversed(terms):
        objective+=weight*var
        weight*=bound+1
    if weight>=2**62:
        raise ValueError('Hàm mục tiêu quá lớn; cần chuyển sang giải tuần tự.')
    m.minimize(objective)
    return WorkforceObjective(total,excess,missing,admin_t,admin_counts,shift_excess,admin_total,spread,sd,ad,objective)
