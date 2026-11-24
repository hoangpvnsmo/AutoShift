"""Bước 4.2: chuỗi tối ưu stage 3 -> E01 -> E02, tái sử dụng các module 4.1."""
from dataclasses import dataclass
from math import isfinite
from core.optimization.solver import solve
from core.optimization.objective import lock_workforce
from core.validation.admin_checks import check_admin_credits
from core.objectives.quality_checks import workforce_score, shift_balance_rows, shift_balance_score
from core.objectives.soft_constraints import add_shift_balance
from core.objectives.night_constraints import add_night_balance
from core.objectives.night_checks import night_rows,night_score,night_lower_bound

@dataclass
class Layer:
    status: str
    result: object | None
    before: tuple | None
    after: tuple | None
    note: str

@dataclass
class Quality42:
    baseline: object
    selected: object
    workforce: tuple | None
    e01: Layer | None
    e02: Layer | None


def flag(data,name):
    enabled=data.rules.get('optimization',{}).get(name,True)
    if type(enabled) is not bool: raise ValueError(f'optimization.{name} phải true/false.')
    return enabled


def score_workforce(ctx,data,a):
    errors,_,rows=check_admin_credits(data,ctx.days,a)
    if errors: raise RuntimeError('\n'.join(errors))
    return workforce_score(rows)


def hint(ctx,assignments):
    # Xóa hint lượt cũ; CP-SAT không cho cùng một biến xuất hiện 2 lần trong hint.
    ctx.model.clear_hints()
    for (i,d,s),v in ctx.x.items(): ctx.model.add_hint(v,int(assignments[i,d]==s))
    for (i,d),v in ctx.worked.items(): ctx.model.add_hint(v,int(assignments[i,d]!=0))


def refine(ctx,selected,score,objective,limit,verify):
    """Giữ nghiệm tốt nhất theo tuple điểm; verify kiểm tra mục tiêu ưu tiên đã khóa."""
    before=score(selected.assignments)
    ctx.model.minimize(objective)
    hint(ctx,selected.assignments)
    result=solve(ctx,time_limit=limit)
    if result.status in ('INFEASIBLE','MODEL_INVALID'):
        raise RuntimeError(f'{result.status} dù có nghiệm nền; kiểm tra mô hình/hàm khóa.')
    if result.assignments is None:
        return selected,Layer(result.status,result,before,before,'Chưa có nghiệm mới; giữ lịch trước.')
    verify(result.assignments)
    after=score(result.assignments)
    if after>before:
        if result.status=='OPTIMAL': raise RuntimeError('Nghiệm tối ưu kém hơn nghiệm nền.')
        return selected,Layer(result.status,result,before,before,'Nghiệm mới kém hơn; giữ lịch trước.')
    return result,Layer(result.status,result,before,after,'Chấp nhận nghiệm không kém hơn lịch trước.')


def solve_quality42(ctx,data,credit,demand,base_time=120,quality_time=120,night_time=120):
    """ctx phải được tạo bằng build_stage3(data); demand là nhu cầu chính xác của mô hình."""
    if any(not isfinite(t) or t<=0 for t in (base_time,quality_time,night_time)):
        raise ValueError('Giới hạn thời gian phải hữu hạn và > 0.')
    e01_enabled=flag(data,'balance_shift_types'); e02_enabled=flag(data,'balance_night_shifts')
    baseline=solve(ctx,time_limit=base_time)
    if baseline.assignments is None: return Quality42(baseline,baseline,None,None,None)
    selected=baseline; workforce=score_workforce(ctx,data,baseline.assignments)
    lock_workforce(ctx,credit,workforce)
    def verify_workforce(a):
        if score_workforce(ctx,data,a)!=workforce: raise RuntimeError('Mục tiêu stage 3 bị thay đổi.')
    def s1(a): return shift_balance_score(shift_balance_rows(data,ctx.days,a))
    def s2(a): return night_score(night_rows(data,ctx.days,a))
    e01=None; quality=None
    if e01_enabled:
        quality=add_shift_balance(ctx,data)
        selected,e01=refine(ctx,selected,s1,quality.objective,quality_time,verify_workforce)
        ctx.model.add(quality.worst_gap==e01.after[0]); ctx.model.add(quality.total_gap==e01.after[1])
    else:
        e01=Layer('DISABLED',None,s1(selected.assignments),s1(selected.assignments),'E01 tắt; không khóa điểm E01.')
    def verify(a):
        verify_workforce(a)
        if e01_enabled and s1(a)!=e01.after: raise RuntimeError('E02 làm thay đổi điểm E01.')
    before=s2(selected.assignments)
    if not e02_enabled:
        e02=Layer('DISABLED',None,before,before,'E02 tắt trong cấu hình.')
    elif before==night_lower_bound(data,demand):
        e02=Layer('PROVEN_BOUND',None,before,before,'Đã đạt cận dưới toán học E02; không cần lượt giải 3.')
    else:
        night=add_night_balance(ctx,data,quality.counts if quality else None)
        selected,e02=refine(ctx,selected,s2,night.objective,night_time,verify)
    verify(selected.assignments)
    # Nếu E01 bị tắt, thống kê sau E02 có thể thay đổi hợp lệ.
    if not e01_enabled: e01.after=s1(selected.assignments)
    return Quality42(baseline,selected,workforce,e01,e02)
