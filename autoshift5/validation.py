"""Independent full-code repair and boundary validation. No Solver imports."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, time, timedelta
from core.data.calendar_utils import month_days, standard_credit
from core.objectives.rotation_checks import preferred_rest_minutes
from core.validation.transition_checks import transition_config
from validator.quality import validate_quality, score_vector
from .domain import assert_grid, changed_cells, history_gaps, LOCAL_TZ

SHIFT = {'1', '2', '3'}


def shift_start(data, day, code):
    if code not in SHIFT:
        # Unknown nonshift hours: conservatively lock at local midnight.
        return datetime.combine(day, time(), LOCAL_TZ)
    minutes = transition_config(data).intervals[int(code)][0]
    return datetime.combine(day, time(), LOCAL_TZ) + timedelta(minutes=minutes)


def frozen_cell(data, key, baseline, as_of, locks, allowed):
    return key in locks or key[1] not in allowed or shift_start(data, key[1], baseline[key]) <= as_of


def short_events(data, codes):
    cfg = transition_config(data)
    threshold = preferred_rest_minutes(data)
    events = set()
    for e in data.employees:
        if e.group != 'SHIFT':
            continue
        for d in month_days(data.year, data.month):
            prev = d - timedelta(days=1)
            a, b = codes.get((e.id, prev), data.previous.get((e.id, prev))), codes[e.id, d]
            if a in SHIFT and b in SHIFT and a != b and 1440+cfg.intervals[int(b)][0]-cfg.intervals[int(a)][1] < threshold:
                events.add((e.id, d))
    return events


def cell_weight(day, as_of, published):
    return max(1, 32-(day-as_of.astimezone(LOCAL_TZ).date()).days) + (31 if published else 0)


def repair_metrics(data, baseline, codes, *, direct_ids=(), as_of, published=False, annual=None, prefs=(), work_policy=None):
    assert_grid(data, baseline)
    assert_grid(data, codes)
    changes = changed_cells(baseline, codes)
    affected = {r['employee_id'] for r in changes}
    totals = lambda cells: {e.id: sum(data.rules['work_credit'][cells[e.id, d]] for d in month_days(data.year, data.month)) for e in data.employees}
    before, after = totals(baseline), totals(codes)
    targets = {e.id: standard_credit(data) if e.target_credit is None else e.target_credit for e in data.employees}
    excess = lambda counts: sum(max(0, v-targets[i]) for i, v in counts.items())
    admin = {e.id for e in data.employees if e.group == 'ADMIN'}
    supports = lambda cells: sum(c in SHIFT for (i, d), c in cells.items() if i in admin)
    new_short = short_events(data, codes) - short_events(data, baseline)
    quality = validate_quality(data, codes, prefs=prefs, work_policy=work_policy, source='repair_independent')
    q = score_vector(quality)
    if q is None:
        raise ValueError('Không tính đủ điểm chất lượng độc lập.')
    vector = [len(changes), len(affected-set(direct_ids)),
              sum(cell_weight(datetime.fromisoformat(c['day']).date(), as_of, published) for c in changes),
              [max(0, excess(after)-excess(before)), max(0, supports(codes)-supports(baseline))],
              len(new_short), sum(abs(after[i]-before[i]) for i in before),
              sum(codes[k] != annual[k] for k in codes) if annual is not None else 0,
              [list(row) for row in q]]
    return dict(vector=vector, changed_cells=len(changes), affected_people=len(affected), other_people=vector[1],
                unchanged_ratio=1-len(changes)/len(codes), affected_days=len({c['day'] for c in changes}),
                work_before=before, work_after=after, overtime_before=excess(before), overtime_after=excess(after),
                admin_before=supports(baseline), admin_after=supports(codes), new_short_rest=len(new_short), changes=changes), quality


def validate_repair(data, baseline, codes, *, direct_ids=(), as_of, locks=(), allowed=(), published=False,
                    annual=None, prefs=(), work_policy=None, expected=None, max_changes=None,
                    no_admin_increase=False, no_overtime_increase=False):
    metrics, quality = repair_metrics(data, baseline, codes, direct_ids=direct_ids, as_of=as_of, published=published,
                                      annual=annual, prefs=prefs, work_policy=work_policy)
    errors = [f'{f.rule_id}: {f.message}' for f in quality.errors]
    for key in codes:
        if frozen_cell(data, key, baseline, as_of, set(locks), set(allowed)) and codes[key] != baseline[key]:
            errors.append(f'Ô khóa/đã bắt đầu/ngoài phạm vi bị đổi: {key}')
        if codes[key] != baseline[key] and codes[key] in SHIFT and shift_start(data, key[1], codes[key]) <= as_of:
            errors.append(f'Bổ sung ca đã bắt đầu: {key}')
    if max_changes is not None and metrics['changed_cells'] > max_changes:
        errors.append('Vượt giới hạn số ô thay đổi.')
    if no_admin_increase and metrics['admin_after'] > metrics['admin_before']:
        errors.append('Tăng huy động ADMIN.')
    if no_overtime_increase and metrics['overtime_after'] > metrics['overtime_before']:
        errors.append('Tăng công vượt.')
    if expected is not None and metrics['vector'] != expected:
        errors.append('Vector REPAIR từ Solver không khớp tính độc lập.')
    if errors:
        raise ValueError('; '.join(errors[:20]))
    return metrics, quality


def validate_month(data, codes, prefs=(), work_policy=None):
    assert_grid(data, codes)
    r = validate_quality(data, codes, prefs=prefs, work_policy=work_policy)
    if r.errors:
        raise ValueError('; '.join(f'{f.rule_id} ID={f.employee_id} {f.day}: {f.message}' for f in r.errors[:12]))
    return r


def boundary_report(left_data, left_codes, right_data, right_codes, *, prefs=(), work_policy=None):
    """Check the entire next month with history overlaid from the proposed left month."""
    from .domain import history_length
    ld = month_days(left_data.year, left_data.month)
    rd = month_days(right_data.year, right_data.month)
    if ld[-1] + timedelta(days=1) != rd[0]:
        raise ValueError('Kiểm tra biên cần hai tháng liên tiếp.')
    prev = dict(right_data.previous)
    for e in right_data.employees:
        for k in range(1, history_length(right_data)+1):
            d = rd[0]-timedelta(days=k)
            if (e.id, d) not in left_codes:
                raise ValueError(f'Thiếu mã lịch sử liên kết: ID {e.id}, {d}. Không suy diễn OFF.')
            prev[e.id, d] = left_codes[e.id, d]
    linked = replace(right_data, previous=prev)
    report = validate_quality(linked, right_codes, prefs=prefs, work_policy=work_policy, source='linked_month_boundary')
    first = rd[0]
    seam_singles = []
    for e in left_data.employees:
        i = e.id
        if e.group == 'SHIFT' and (i, first) in right_codes:
            a, b, c = left_codes[i, ld[-2]], left_codes[i, ld[-1]], right_codes[i, first]
            if a != 'OFF' and b == 'OFF' and c != 'OFF':
                seam_singles.append(i)
    return dict(left=f'{left_data.year:04d}-{left_data.month:02d}', right=f'{right_data.year:04d}-{right_data.month:02d}',
                verdict=report.verdict, errors=len(report.errors), hard_pending=list(report.hard_pending),
                seam_isolated_off=seam_singles, quality=[list(x) for x in score_vector(report)] if score_vector(report) else None,
                report=report.to_dict())
