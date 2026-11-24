"""Validated result data and console presentation. No checkpoint writes here."""
from dataclasses import dataclass
from core.engine.engine_config import STAGES, KEYS
from core.validation.admin_checks import check_admin_credits
from core.validation.credit_checks import check_staffing
from core.validation.sequence_checks import check_sequences
from core.validation.transition_checks import check_transitions
from core.objectives.quality_checks import shift_balance_rows, shift_balance_score, workforce_score
from core.objectives.night_checks import night_rows, night_score
from core.objectives.day_balance_checks import day_sets, day_balance_rows
from core.objectives.day_balance_v2 import day_score_v2, day_bound
from core.objectives.rest_checks import rest_windows, rest_rows, rest_scores
from core.objectives.rotation_checks import preferred_rest_minutes
from core.objectives.flexible_rotation import flexible_rows, flexible_score
from core.objectives.preference_checks import preference_rows, preference_score

@dataclass(frozen=True)
class ScheduleReport:
    codes: dict
    credit_rows: tuple
    shift_rows: tuple
    night_groups: tuple
    day_rows: tuple
    holidays: tuple
    weekends: tuple
    holiday_bound: object
    weekend_bound: object
    rest_windows: object
    rest_rows: tuple
    rotation_rows: tuple
    preference_items: tuple
    scores: tuple
    total_demand: int
    shift_count: int
    preferred_rest_hours: float


def collect_report(data,days,demand,a,prefs,q):
    """Verify existing A/B/C/D rules and all objective scores before persistence.
    This is not yet the independent Stage 5 validator (CT/DT/T/BKD hours pending).
    """
    if set(a)!={(e.id,d) for e in data.employees for d in days}:
        raise RuntimeError('Thiếu/thừa ô lịch.')
    errors,codes,rows=check_admin_credits(data,days,a)
    errors=list(errors)+check_staffing(data,days,a)+check_sequences(data,days,a)[0]+check_transitions(data,days,a)[0]
    if sum(r.shifts for r in rows)!=sum(demand.values()): errors.append('Tổng lượt trực không khớp nhu cầu.')
    if errors: raise RuntimeError('\n'.join(errors))
    sr=shift_balance_rows(data,days,a); nr=night_rows(data,days,a)
    h,w=day_sets(data,days); dr=day_balance_rows(data,days,a)
    for kind,dates in (('holiday',h),('weekend',w)):
        if sum(getattr(r,kind) for r in dr)!=sum(v for (d,s),v in demand.items() if d in dates):
            raise RuntimeError(f'Tổng lượt {kind} không đúng nhu cầu.')
    rw=rest_windows(data,days); rr=rest_rows(data,days,a,rw)
    sa,sb=rest_scores(rr,len(rw.weekends)); rot=flexible_rows(data,days,a); pr=preference_rows(data,days,a,prefs)
    scores=(workforce_score(rows),shift_balance_score(sr),night_score(nr),day_score_v2(dr,'holiday'),day_score_v2(dr,'weekend'),sa,sb,flexible_score(rot),preference_score(pr))
    if scores[0]!=q.workforce: raise RuntimeError('Mục tiêu giai đoạn 3 bị thay đổi.')
    for j,key in enumerate(KEYS,1):
        layer=getattr(q,key)
        if layer is None or scores[j]!=layer.after: raise RuntimeError(f'Sai điểm {key} cuối cùng.')
        if layer.status!='DISABLED' and layer.after>layer.before: raise RuntimeError(f'{key} kém hơn lịch nền.')
    return ScheduleReport(codes,tuple(rows),tuple(sr),tuple(nr),tuple(dr),tuple(h),tuple(w),day_bound(data,h,demand,q.workforce[1]),day_bound(data,w,demand,q.workforce[1]),rw,tuple(rr),tuple(rot),tuple(pr),scores,sum(demand.values()),len(demand),preferred_rest_minutes(data)/60)


def print_layers(q,emit=print):
    emit('Lượt 1 (giai đoạn 3):',q.baseline.status,'|',round(q.baseline.wall_time,2),'s')
    for j,spec in enumerate(STAGES[1:],2):
        layer=getattr(q,spec.key)
        if layer is not None:
            elapsed=round(layer.result.wall_time,2) if layer.result is not None else 0
            emit(f'Lượt {j} ({spec.label}): {layer.status} | {elapsed} s | {layer.note}')
    if q.proven: emit('Đã chứng minh tối ưu các tầng đang bật.')
    elif q.selected.assignments is not None: emit('Nghiệm hợp lệ theo mô hình; chưa chứng minh tối ưu toàn bộ các tầng đang bật.')


def print_report(report,days,q,emit=print):
    r=report; rows=r.credit_rows; codes=r.codes
    shift=[x for x in rows if x.group=='SHIFT']; admin=[x for x in rows if x.group=='ADMIN']
    emit('\nLịch 7 ngày đầu (SHIFT + ADMIN):')
    emit('ID Nhóm  '+' '.join(f'{d.day:>3}' for d in days[:7]))
    for x in rows: emit(f'{x.id:>2} {x.group:<5} '+' '.join(f'{codes[x.id,d]:>3}' for d in days[:7]))
    emit('\nID Nhóm  Chuẩn Ca1 Ca2 Ca3 CT DT BKD  T OFF Tổng Dưới Vượt')
    for x in rows: emit(f'{x.id:>2} {x.group:<5} {x.target:>5} {x.night:>3} {x.morning:>3} {x.afternoon:>3} {x.ct:>2} {x.dt:>2} {x.bkd:>3} {x.t:>2} {x.off:>3} {x.total:>4} {x.missing:>4} {x.excess:>4}')
    emit('\nTổng lượt trực:',sum(x.shifts for x in rows))
    emit('Tổng lượt trực SHIFT:',sum(x.shifts for x in shift))
    emit('Tổng công SHIFT:',sum(x.total for x in shift))
    emit('Chênh lệch công SHIFT:',max((x.total for x in shift),default=0)-min((x.total for x in shift),default=0))
    emit('Tổng công vượt SHIFT:',sum(x.excess for x in shift))
    emit('Tổng lượt huy động ADMIN:',sum(x.shifts for x in admin))
    emit('Tổng công T ADMIN:',sum(x.t for x in admin))
    emit('Tổng ca ngày lễ ADMIN:',sum(x.holiday_shifts for x in admin))
    emit('Tổng công vượt ADMIN:',sum(x.excess for x in admin))
    if any(x.shifts for x in admin):
        emit('Danh sách ca hỗ trợ:'); ids={x.id for x in admin}
        for (i,d),s in sorted(q.selected.assignments.items(),key=lambda item:(item[0][1],item[0][0])):
            if s and i in ids: emit(f'  {d} | ID {i} | Ca {s}')
    if any(x.excess for x in rows): emit('CẢNH BÁO: Có công vượt chuẩn; cần xem xét/phê duyệt.')
    if q.baseline.status!='OPTIMAL': emit('CẢNH BÁO: Mục tiêu giai đoạn 3 chưa được chứng minh tối ưu.')
    emit('5 chỉ tiêu giai đoạn 3 (giữ nguyên):',q.workforce)
    emit('\nCân bằng loại ca SHIFT:')
    emit('ID  Ca1 Ca2 Ca3  Chênh lệch  Ca được phép')
    for x in r.shift_rows: emit(f'{x.id:>2}  {x.counts[0]:>3} {x.counts[1]:>3} {x.counts[2]:>3} {x.gap:>10}  {x.allowed}')
    emit('Chênh lệch lớn nhất trước -> sau:',q.e01.before[0],'->',q.e01.after[0])
    emit('Tổng chênh lệch trước -> sau:',q.e01.before[1],'->',q.e01.after[1])
    emit('\nCân bằng ca đêm E02:')
    for j,g in enumerate(r.night_groups,1):
        emit(f'Nhóm {j}: quyền ca {g.allowed} | chuẩn {g.target} | {len(g.counts)} người')
        emit('  '+'; '.join(f'ID {i}: {n}' for i,n in g.counts))
        emit('  Tổng ca đêm:',sum(n for _,n in g.counts),'| Chênh lệch:',g.gap)
    if not r.night_groups: emit('Không có SHIFT đủ điều kiện đi đêm.')
    emit('Điểm E02 (max, tổng) trước -> sau:',q.e02.before,'->',q.e02.after)
    emit('\nCân bằng trực lễ và cuối tuần E03:')
    emit('Ngày lễ:',', '.join(map(str,r.holidays)) or '(không có)')
    emit('Ngày cuối tuần:',', '.join(map(str,r.weekends)) or '(không có)')
    emit('ID Nhóm  Trực lễ  Trực cuối tuần')
    for x in r.day_rows: emit(f'{x.id:>2} {x.group:<5} {x.holiday:>7} {x.weekend:>15}')
    for kind,layer,bound in [('holiday',q.holiday,r.holiday_bound),('weekend',q.weekend,r.weekend_bound)]:
        emit(f'{kind} | chênh lệch SHIFT trước -> sau: {layer.before[0]} -> {layer.after[0]}')
        emit('  Cận (gap, độ lệch nguyên):',bound.lower,'| Điểm trước -> sau:',layer.before,'->',layer.after)
        emit('  Số lượt SHIFT tối thiểu/tối đa:',bound.total_min,bound.total_max,'| Lượt lặp A07 tối thiểu:',bound.overlap)
        emit('  SHIFT không trực nhóm ngày này:',sum(getattr(x,kind)==0 for x in r.day_rows if x.group=='SHIFT'))
        emit('  Tổng lượt SHIFT:',sum(getattr(x,kind) for x in r.day_rows if x.group=='SHIFT'),'| ADMIN:',sum(getattr(x,kind) for x in r.day_rows if x.group=='ADMIN'),'| Toàn phòng:',sum(getattr(x,kind) for x in r.day_rows))
    emit('\nPhân bố ngày nghỉ E04 (SHIFT):')
    emit('Cuối tuần đủ dữ liệu:',len(r.rest_windows.weekends),'| Chờ dữ liệu:',r.rest_windows.pending_weekends)
    emit('ID  OFF  OFF lẻ  OFF dư sau 3  Đợt >=2  Cuối tuần trọn')
    for x in r.rest_rows: emit(f'{x.id:>2} {x.off:>4} {x.singles:>7} {x.long_extra:>13} {x.blocks:>8} {x.full_weekends:>15}')
    emit('E04a (tổng OFF lẻ, max OFF lẻ, OFF dư sau 3):',q.rest_a.before,'->',r.scores[5])
    emit('E04b (người chưa có cuối tuần trọn, lượt cuối tuần không trọn, gap cuối tuần trọn, gap đợt >=2):',q.rest_b.before,'->',r.scores[6])
    emit('PASS: E04 giữ các điểm ưu tiên trước; điểm nghỉ được kiểm tra độc lập.')
    emit('PASS: giữ 5 chỉ tiêu stage 3 và các điểm ưu tiên đã khóa; E01/E02/E03 không kém hơn ở tầng đang bật.')
    emit(f'PASS: {r.shift_count} ca đúng nhu cầu; nhóm A/B/C và rule cứng D; công T/ADMIN.')
    emit('Cận từ điển E04b:',q.rest_b_lower)
    emit('\nE05 – Luân phiên linh hoạt (SHIFT, gồm biên tháng trước)')
    emit('Ngưỡng nghỉ mềm:',r.preferred_rest_hours,'giờ')
    emit('ID  Chuyển ca  Nghỉ ngắn  Dư chuỗi >2  Dư cửa sổ 7 ngày  Cặp ngày trực liền')
    for x in r.rotation_rows: emit(f'{x.id:>2} {x.changes:>10} {x.short:>10} {x.streak:>13} {x.concentration:>17} {x.adjacent:>19}')
    emit('E05 mới (nghỉ ngắn, dư chuỗi, dư cửa sổ, max bất lợi/người):',q.rotation.before,'->',r.scores[7])
    emit('PASS: E05 giữ điểm các tầng đang bật; thống kê độc lập khớp mô hình.')
    emit('\nE06 – Nguyện vọng mềm (sau E05):')
    emit('ID Ngày       Nguyện vọng       Ca Trọng số Lịch Kết quả')
    for p,c,ok,impossible in r.preference_items:
        reason='Đạt' if ok else ('Không đạt (xung đột rule cứng/quyền ca)' if impossible else 'Không đạt (chưa đáp ứng trong nghiệm chọn)')
        emit(f'{p.employee_id:>2} {p.day} {p.kind:<16} {str(p.shift or "-"):>2} {p.weight:>8} {c:>4} {reason}')
    emit('E06 (tổng trọng số không đáp ứng):',q.preferences.before,'->',r.scores[8],'| Cận:',q.preference_lower)
    emit('Đáp ứng:',sum(ok for p,c,ok,impossible in r.preference_items),'/',len(r.preference_items),'| Tổng trọng số:',sum(p.weight for p,c,ok,impossible in r.preference_items))
    emit('PASS: E06 giữ điểm các tầng ưu tiên đang bật, gồm E05.')
