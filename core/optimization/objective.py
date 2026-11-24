"""Giải theo tầng ưu tiên, giữ nguyên kết quả mục tiêu giai đoạn 3."""
from dataclasses import dataclass
from core.optimization.solver import solve
from core.validation.admin_checks import check_admin_credits
from core.objectives.quality_checks import workforce_score, shift_balance_rows, shift_balance_score
from core.objectives.soft_constraints import add_shift_balance

@dataclass
class QualityResult:
    baseline: object
    refinement: object | None
    selected: object
    workforce: tuple | None
    before: tuple | None
    after: tuple | None
    note: str


def workforce_terms(credit):
    return (credit.shift_excess, credit.admin_total, credit.shift_spread,
            credit.shift_deviation, credit.admin_deviation)


def lock_workforce(ctx, credit, score):
    if len(score) != 5:
        raise ValueError('Cần đủ 5 giá trị mục tiêu giai đoạn 3.')
    for var, value in zip(workforce_terms(credit), score):
        ctx.model.add(var == value)


def _scores(data, ctx, assignments):
    errors, _, rows = check_admin_credits(data,ctx.days,assignments)
    if errors:
        raise RuntimeError('\n'.join(errors))
    return workforce_score(rows), shift_balance_score(shift_balance_rows(data,ctx.days,assignments))


def solve_shift_quality(ctx, data, credit, base_time=120, quality_time=120):
    """Chạy stage 3 trước, khóa 5 giá trị, rồi tối ưu E01. Không khóa từng lịch/công cá nhân."""
    enabled = data.rules.get('optimization',{}).get('balance_shift_types',True)
    if type(enabled) is not bool:
        raise ValueError('optimization.balance_shift_types phải là true/false.')
    if base_time <= 0 or quality_time <= 0:
        raise ValueError('Giới hạn thời gian phải > 0.')
    baseline = solve(ctx,time_limit=base_time)
    if baseline.assignments is None:
        return QualityResult(baseline,None,baseline,None,None,None,'Giai đoạn 3 chưa có nghiệm.')
    workforce, before = _scores(data,ctx,baseline.assignments)
    if not enabled:
        return QualityResult(baseline,None,baseline,workforce,before,before,'E01 đang tắt trong cấu hình.')
    lock_workforce(ctx,credit,workforce)
    quality = add_shift_balance(ctx,data)
    # minimize thay thế objective cũ; các mục tiêu cũ đã bị khóa chính xác.
    ctx.model.minimize(quality.objective)
    for (i,d,s),var in ctx.x.items():
        ctx.model.add_hint(var,int(baseline.assignments[i,d]==s))
    for (i,d),var in ctx.worked.items():
        ctx.model.add_hint(var,int(baseline.assignments[i,d]!=0))
    refined = solve(ctx,time_limit=quality_time)
    if refined.assignments is None:
        if refined.status in ('INFEASIBLE','MODEL_INVALID'):
            raise RuntimeError(f'E01: {refined.status} dù đã có nghiệm nền; kiểm tra mô hình.')
        return QualityResult(baseline,refined,baseline,workforce,before,before,'E01 chưa có nghiệm mới; giữ lịch nền.')
    actual, after = _scores(data,ctx,refined.assignments)
    if actual != workforce:
        raise RuntimeError('E01 làm thay đổi mục tiêu giai đoạn 3.')
    if after > before:
        if refined.status == 'OPTIMAL':
            raise RuntimeError('Nghiệm tối ưu E01 lại kém hơn nghiệm nền; kiểm tra mô hình.')
        return QualityResult(baseline,refined,baseline,workforce,before,before,'E01 chưa cải thiện; giữ lịch nền.')
    note = ('Đã chứng minh tối ưu cả hai lượt.' if baseline.status == refined.status == 'OPTIMAL'
            else 'Nghiệm hợp lệ; chưa chứng minh tối ưu toàn bộ các tầng.')
    return QualityResult(baseline,refined,refined,workforce,before,after,note)
