"""Full-code, lexicographic adaptive CP-SAT repair, independent of FRESH/RESUME."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from time import perf_counter
from ortools.sat.python import cp_model
from core.model.engine_model import build_stage3
from core.validation.credit_checks import request_map
from core.objectives.rotation_checks import preferred_rest_minutes
from core.validation.transition_checks import transition_config
from .domain import CODE_ID, CODES, assert_grid, numeric, LOCAL_TZ
from .validation import frozen_cell, shift_start, short_events, cell_weight, repair_metrics, validate_repair
from .quality_model import quality_terms


@dataclass(frozen=True)
class RepairOptions:
    as_of: datetime
    budget: float = 60
    mode: str = 'FAST'
    allowed_from: date | None = None
    allowed_until: date | None = None
    max_changes: int | None = None
    no_admin_increase: bool = False
    no_overtime_increase: bool = False
    published: bool = False
    disabled_layers: tuple[str, ...] = ()

    def __post_init__(self):
        from math import isfinite
        if self.as_of.tzinfo is None:
            raise ValueError('as_of cần múi giờ rõ ràng, ví dụ +07:00.')
        if not isfinite(self.budget) or self.budget <= 0 or self.mode not in ('FAST', 'OPTIMAL'):
            raise ValueError('Ngân sách phải dương, mode FAST/OPTIMAL.')
        if self.max_changes is not None and (type(self.max_changes) is not int or self.max_changes < 0):
            raise ValueError('max_changes cần số nguyên không âm.')
        if set(self.disabled_layers)-{f'R{k}' for k in range(2, 9)}:
            raise ValueError('Chỉ cho phép tắt R2..R8; R1 luôn là ưu tiên ổn định bắt buộc.')


@dataclass
class RepairResult:
    status: str
    codes: dict | None = None
    metrics: dict | None = None
    report: object = None
    proof: dict = field(default_factory=dict)
    attempts: list = field(default_factory=list)


def full_code_variables(ctx, data):
    req = request_map(data)
    people = {e.id: e for e in data.employees}
    out = {}
    for key, worked in ctx.worked.items():
        i, d = key
        c = req.get(key)
        if c not in ('OFF', 'CT', 'DT', 'BKD'):
            c = 'T' if people[i].group == 'ADMIN' and d.weekday()<5 and d not in data.holidays else 'OFF'
        v = ctx.model.new_int_var(1, len(CODES), f'full_code_{i}_{d}')
        ctx.model.add(v == sum(CODE_ID[str(s)]*ctx.x[i, d, s] for s in ctx.shifts) + CODE_ID[c]*(1-worked))
        out[key] = v
    return out


def indicator_difference(model, var, value, name):
    z = model.new_bool_var(name)
    model.add(var != value).only_enforce_if(z)
    model.add(var == value).only_enforce_if(~z)
    return z


def build_repair(data, baseline, *, options, scope, direct_ids=(), locks=(), annual=None, prefs=()):
    assert_grid(data, baseline)
    if annual is not None:
        assert_grid(data, annual)
    ctx, _, _, credit = build_stage3(data)
    m = ctx.model
    m.clear_objective()
    values = full_code_variables(ctx, data)
    z = {k: indicator_difference(m, v, CODE_ID[baseline[k]], f'changed_{k[0]}_{k[1]}') for k, v in values.items()}
    for key, v in values.items():
        if frozen_cell(data, key, baseline, options.as_of, set(locks), set(scope)):
            m.add(v == CODE_ID[baseline[key]])
        for shift in ctx.shifts:
            if baseline[key] != str(shift) and shift_start(data, key[1], str(shift)) <= options.as_of:
                m.add(ctx.x[key[0], key[1], shift] == 0)
    affected = []
    for e in data.employees:
        if e.id not in direct_ids:
            a = m.new_bool_var(f'other_affected_{e.id}')
            m.add_max_equality(a, [z[e.id, d] for d in ctx.days])
            affected.append(a)
    D, N = len(ctx.days), len(data.employees)
    def positive(expr, cap, name):
        v = m.new_int_var(0, cap, name)
        m.add_max_equality(v, [0, expr])
        return v
    base_totals = {e.id: sum(data.rules['work_credit'][baseline[e.id, d]] for d in ctx.days) for e in data.employees}
    from core.validation.credit_checks import target_credit
    base_excess = sum(max(0, base_totals[e.id]-target_credit(data, e)) for e in data.employees)
    base_admin = sum(c in ('1', '2', '3') for (i, d), c in baseline.items() if any(e.id==i and e.group=='ADMIN' for e in data.employees))
    overtime = positive(sum(credit.excess.values())-base_excess, N*D, 'new_overtime')
    admin = positive(credit.admin_total-base_admin, N*D, 'new_admin')
    if options.no_admin_increase:
        m.add(credit.admin_total <= base_admin)
    if options.no_overtime_increase:
        m.add(sum(credit.excess.values()) <= base_excess)
    if options.max_changes is not None:
        m.add(sum(z.values()) <= options.max_changes)
    cfg = transition_config(data)
    threshold = preferred_rest_minutes(data)
    old_short = short_events(data, baseline)
    new_short = []
    for e in data.employees:
        if e.group != 'SHIFT':
            continue
        for d in ctx.days:
            if (e.id, d) in old_short:
                continue
            prev = d-timedelta(days=1)
            for a in ctx.shifts:
                for b in ctx.shifts:
                    if a==b or 1440+cfg.intervals[b][0]-cfg.intervals[a][1] >= threshold:
                        continue
                    x = ctx.x[e.id, prev, a] if prev in ctx.days else int(data.previous.get((e.id, prev)) == str(a))
                    y = ctx.x[e.id, d, b]
                    v = m.new_bool_var(f'new_short_{e.id}_{d}_{a}_{b}')
                    m.add(v<=x)
                    m.add(v<=y)
                    m.add(v>=x+y-1)
                    new_short.append(v)
    work_diff = []
    for e in data.employees:
        v = m.new_int_var(0, D, f'work_difference_{e.id}')
        m.add_abs_equality(v, credit.total[e.id]-base_totals[e.id])
        work_diff.append(v)
    annual_diff = [indicator_difference(m, v, CODE_ID[annual[k]], f'annual_diff_{k[0]}_{k[1]}') for k, v in values.items()] if annual is not None else []
    quality, quality_active = quality_terms(ctx, data, credit, prefs)
    groups = [(sum(z.values()),), (sum(affected),),
              (sum(cell_weight(d, options.as_of, options.published)*v for (i, d), v in z.items()),),
              (overtime, admin), (sum(new_short),), (sum(work_diff),), (sum(annual_diff),)]
    terms = [(f'R{j+1}', t) for j, group in enumerate(groups) if f'R{j+1}' not in options.disabled_layers for t in group]
    if 'R8' not in options.disabled_layers:
        terms.extend((f'R8.{j}', t) for j, group in enumerate(quality) if quality_active[j] for t in group)
    return ctx, values, groups, quality, quality_active, terms


def comparison_key(metrics, options, quality_active):
    v = metrics['vector']
    result = [tuple(v[j]) if isinstance(v[j], list) else v[j] for j in range(7) if f'R{j+1}' not in options.disabled_layers]
    if 'R8' not in options.disabled_layers:
        result.append(tuple(tuple(row) for row, active in zip(v[7], quality_active) if active))
    return tuple(result)


def repair_schedule(data, baseline, *, options, direct_cells=(), locks=(), annual=None, prefs=(), work_policy=None,
                    emit=lambda *a: None, cancelled=lambda: False, boundary_check=None):
    assert_grid(data, baseline)
    direct_cells = set(direct_cells)
    if not direct_cells <= set(baseline):
        raise ValueError('Yêu cầu REPAIR không thuộc tháng/nhân sự.')
    direct_ids = {i for i, d in direct_cells}
    from core.data.calendar_utils import month_days
    days = month_days(data.year, data.month)
    allowed = {d for d in days if (options.allowed_from is None or d>=options.allowed_from) and (options.allowed_until is None or d<=options.allowed_until)}
    centers = {d for i, d in direct_cells}
    try:
        metrics, report = validate_repair(data, baseline, baseline, direct_ids=direct_ids, as_of=options.as_of,
            locks=locks, allowed=allowed, published=options.published, annual=annual, prefs=prefs, work_policy=work_policy,
            max_changes=options.max_changes, no_admin_increase=options.no_admin_increase,
            no_overtime_increase=options.no_overtime_increase)
        if boundary_check is not None:
            metrics['boundary'] = boundary_check(baseline)
        return RepairResult('FEASIBLE', dict(baseline), metrics, report,
                            {'status':'R1_GLOBAL_OPTIMAL','r1_global_optimal':True,'quality_proven':False,
                             'reason':'R1=0 đạt cận dưới toàn cục; mã lịch giữ nguyên 100%.'},
                            [{'radius':None,'status':'NO_CHANGE','days':sorted(map(str,allowed))}])
    except ValueError:
        pass
    scopes = []
    for radius in (0, 1, 3, 7, None):
        scope = allowed if radius is None or not centers else {d for d in allowed if any(abs((d-c).days)<=radius for c in centers)}
        if scope not in [s for _, s in scopes]:
            scopes.append((radius, scope))
    deadline = perf_counter()+options.budget
    best = RepairResult('UNKNOWN')
    any_unknown = False
    for n, (radius, scope) in enumerate(scopes):
        if cancelled():
            best.status = 'CANCELLED'
            return best
        remain = deadline-perf_counter()
        if remain <= .01:
            break
        scope_budget = remain/(len(scopes)-n) if options.mode=='OPTIMAL' else remain/(len(scopes)-n)
        scope_end = min(deadline, perf_counter()+scope_budget)
        ctx, values, groups, quality, quality_active, terms = build_repair(data, baseline, options=options,
            scope=scope, direct_ids=direct_ids, locks=locks, annual=annual, prefs=prefs)
        # A validated incumbent remains usable even if the next solve is UNKNOWN.
        incumbent = None
        try:
            validate_repair(data, baseline, baseline, direct_ids=direct_ids, as_of=options.as_of, locks=locks, allowed=scope,
                            annual=annual, prefs=prefs, work_policy=work_policy, max_changes=options.max_changes,
                            no_admin_increase=options.no_admin_increase, no_overtime_increase=options.no_overtime_increase)
            incumbent = dict(baseline)
        except ValueError:
            pass
        statuses = []
        outcome = 'UNKNOWN'
        for j, (label, term) in enumerate(terms):
            if cancelled():
                break
            remaining = scope_end-perf_counter()
            if remaining <= .01:
                break
            ctx.model.minimize(term)
            ctx.model.clear_hints()
            if incumbent is not None:
                for key, v in values.items():
                    ctx.model.add_hint(v, CODE_ID[incumbent[key]])
                # Restrict to achieved value; a timed-out later layer never worsens it.
                if j == 0:
                    ctx.model.add(term <= sum(incumbent[k] != baseline[k] for k in baseline))
            solver = cp_model.CpSolver()
            solver.parameters.num_search_workers = 8
            solver.parameters.max_time_in_seconds = max(.01, remaining if j==0 else remaining/(len(terms)-j))
            status = solver.solve(ctx.model)
            outcome = solver.status_name(status)
            emit(f'REPAIR vùng {"toàn phạm vi" if radius is None else radius}: {label} {outcome}')
            if status == cp_model.MODEL_INVALID:
                raise ValueError('REPAIR MODEL_INVALID: '+solver.solution_info())
            if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
                any_unknown |= status == cp_model.UNKNOWN
                break
            candidate = {k: CODES[solver.value(v)-1] for k, v in values.items()}
            expected = []
            for index, group in enumerate(groups):
                row = [int(solver.value(v)) if not isinstance(v, int) else v for v in group]
                expected.append(row if index==3 else row[0])
            expected.append([[int(solver.value(v)) for v in group] for group in quality])
            validate_repair(data, baseline, candidate, direct_ids=direct_ids, as_of=options.as_of, locks=locks, allowed=scope,
                            published=options.published, annual=annual, prefs=prefs, work_policy=work_policy, expected=expected,
                            max_changes=options.max_changes, no_admin_increase=options.no_admin_increase,
                            no_overtime_increase=options.no_overtime_increase)
            incumbent = candidate
            point = int(solver.value(term))
            ctx.model.add(term == point)
            statuses.append(dict(layer=label, status=outcome, value=point, bound=solver.best_objective_bound))
        attempt = dict(radius=radius, days=sorted(map(str, scope)), status=outcome, layers=statuses)
        best.attempts.append(attempt)
        if incumbent is None:
            continue
        metrics, report = validate_repair(data, baseline, incumbent, direct_ids=direct_ids, as_of=options.as_of,
            locks=locks, allowed=scope, published=options.published, annual=annual, prefs=prefs, work_policy=work_policy,
            max_changes=options.max_changes, no_admin_increase=options.no_admin_increase, no_overtime_increase=options.no_overtime_increase)
        if boundary_check is not None:
            try:
                metrics['boundary'] = boundary_check(incumbent)
            except ValueError as exc:
                attempt['boundary_error'] = str(exc)
                continue
        full_scope = scope == allowed
        proven_r1 = bool(statuses and statuses[0]['status']=='OPTIMAL' and full_scope)
        if metrics['changed_cells']==0:
            proven_r1 = True  # Global nonnegative lower bound, irrespective of scope.
        proof = dict(status='R1_GLOBAL_OPTIMAL' if proven_r1 else 'RESTRICTED_OR_FEASIBLE',
                     r1_global_optimal=proven_r1, search_scope=attempt, quality_proven=False,
                     r1_certificate={'scope':sorted(map(str,allowed)), 'layer':statuses[0] if statuses else None} if proven_r1 else None)
        if best.codes is None or comparison_key(metrics, options, quality_active) < comparison_key(best.metrics, options, quality_active):
            best.codes, best.metrics, best.report, best.proof = incumbent, metrics, report, proof
        elif best.metrics['vector'][0] == metrics['vector'][0] and proven_r1:
            best.proof['r1_global_optimal'] = True
            best.proof['status'] = 'R1_GLOBAL_OPTIMAL'
            best.proof['r1_certificate'] = {'scope':sorted(map(str,allowed)), 'layer':statuses[0],
                                          'note':'Cận R1 toàn phạm vi bằng R1 của nghiệm đã chọn ở vùng nhỏ.'}
        if options.mode == 'FAST':
            break
    if cancelled():
        best.status = 'CANCELLED'
    elif best.codes is not None:
        best.status = 'FEASIBLE'
    else:
        full = next((a for a in best.attempts if set(a['days'])==set(map(str, allowed))), None)
        best.status = 'INFEASIBLE' if full and full['status']=='INFEASIBLE' else 'UNKNOWN'
    return best
