import unittest
from datetime import date, timedelta
from importlib.util import find_spec
from core.data.models import Employee, MonthData
from core.validation.sequence_checks import check_sequences, sequence_limits, history_codes

HAS_ORTOOLS = find_spec('ortools') is not None
if HAS_ORTOOLS:
    from core.model.model_builder import build_model
    from core.constraints.hard_constraints import (add_consecutive_shift_constraints,
        add_staffing_constraints, add_shift_eligibility, disable_admin_for_trial,
        add_previous_day_continuity)
    from core.optimization.solver import solve


def fixture(history=('OFF',)*5, n=1):
    first = date(2026, 12, 1)
    employees = tuple(Employee(i, f'E{i}', 'SHIFT', i <= 6, (1, 2, 3))
                      for i in range(1, n+1))
    prev = {(i, first-timedelta(days=5-k)): code
            for i in range(1, n+1) for k, code in enumerate(history)}
    rules = {'max_shifts_per_day': 1, 'max_consecutive_night_shifts': 3,
             'max_consecutive_shift_days': 5, 'min_leaders_per_shift': 1,
             'min_previous_day_staff': 1,
             'staff_per_shift': {str(s): {'min':3, 'preferred':4, 'max':4} for s in (1,2,3)}}
    return MonthData(2026, 12, frozenset(), employees, (), prev, rules)


class SequenceCheckerTests(unittest.TestCase):
    def check(self, history, shifts):
        data = fixture(history)
        days = tuple(date(2026,12,1)+timedelta(days=k) for k in range(len(shifts)))
        return check_sequences(data, days, {(1,d):s for d,s in zip(days,shifts)})

    def test_night_violation_at_boundary(self):
        errors, stats = self.check(('OFF','OFF','1','1','1'), [1,0])
        self.assertTrue(any('B01' in err for err in errors))
        self.assertEqual(stats[1], (4,4))

    def test_work_violation_at_boundary(self):
        errors, stats = self.check(('1','1','2','2','3'), [2,0])
        self.assertTrue(any('B03' in err for err in errors))
        self.assertEqual(stats[1], (0,6))

    def test_non_shift_codes_break_streak(self):
        for code in ('CT','DT','BKD','T','OFF'):
            with self.subTest(code=code):
                errors, stats = self.check(('1','1','1','2',code), [1,1,1,2,3,0])
                self.assertFalse(errors)
                self.assertEqual(stats[1], (3,5))

    def test_historical_violation_not_rejected_when_new_day_off(self):
        errors, _ = self.check(('1','1','1','1','1'), [0,1])
        self.assertFalse(errors)

    def test_missing_history(self):
        data = fixture()
        del data.previous[1,date(2026,11,26)]
        with self.assertRaisesRegex(ValueError, 'Previous: thiếu'):
            history_codes(data,1,date(2026,12,1),5)

    def test_invalid_limit(self):
        data = fixture()
        data.rules['max_consecutive_shift_days'] = 0
        with self.assertRaises(ValueError):
            sequence_limits(data)


@unittest.skipUnless(HAS_ORTOOLS, 'Thiếu OR-Tools; cần cài requirements.txt.')
class SequenceSolverTests(unittest.TestCase):
    def solve_fixed(self, codes, history=('OFF',)*5, **limits):
        data = fixture(history)
        data.rules.update(limits)
        ctx = build_model(data)
        add_consecutive_shift_constraints(ctx,data)
        for d,s in zip(ctx.days,codes):
            if s == 0:
                ctx.model.add(ctx.worked[1,d] == 0)
            else:
                ctx.model.add(ctx.x[1,d,s] == 1)
        return solve(ctx, time_limit=10).status

    def test_three_nights_allowed(self):
        self.assertIn(self.solve_fixed([1,1,1,2]), ('OPTIMAL','FEASIBLE'))

    def test_four_nights_forbidden(self):
        self.assertEqual(self.solve_fixed([1,1,1,1]), 'INFEASIBLE')

    def test_five_shift_days_allowed(self):
        self.assertIn(self.solve_fixed([1,1,2,2,3,0,2]), ('OPTIMAL','FEASIBLE'))

    def test_six_shift_days_forbidden(self):
        self.assertEqual(self.solve_fixed([1,1,2,2,3,3]), 'INFEASIBLE')

    def test_boundary_nights(self):
        self.assertEqual(self.solve_fixed([1], ('OFF','OFF','1','1','1')), 'INFEASIBLE')

    def test_boundary_work(self):
        self.assertEqual(self.solve_fixed([2], ('1','1','2','2','3')), 'INFEASIBLE')

    def test_boundary_non_shift_breaks_streak(self):
        for code in ('CT','DT','BKD','T','OFF'):
            with self.subTest(code=code):
                self.assertIn(self.solve_fixed([1,1,1,2,3,0], ('1','1','1','2',code)), ('OPTIMAL','FEASIBLE'))

    def test_configurable_limits(self):
        self.assertEqual(self.solve_fixed([1,1,1], max_consecutive_night_shifts=2), 'INFEASIBLE')
        self.assertEqual(self.solve_fixed([1,2,3], max_consecutive_shift_days=2), 'INFEASIBLE')

    def test_missing_history(self):
        data = fixture()
        del data.previous[1,date(2026,11,26)]
        ctx = build_model(data)
        with self.assertRaisesRegex(ValueError, 'Previous: thiếu'):
            add_consecutive_shift_constraints(ctx,data)

    def test_full_month_with_group_A(self):
        data = fixture(n=16)
        # Lịch ngày cuối tháng có 9 người đã trực, trong đó có 3 phụ trách.
        for i in (1,2,3,7,8,9,10,11,12):
            data.previous[i,date(2026,11,30)] = '2'
        ctx = build_model(data)
        add_staffing_constraints(ctx,data)
        add_shift_eligibility(ctx,data)
        disable_admin_for_trial(ctx,data)
        add_previous_day_continuity(ctx,data)
        add_consecutive_shift_constraints(ctx,data)
        result = solve(ctx,time_limit=30)
        self.assertIn(result.status, ('OPTIMAL','FEASIBLE'))
        errors,_ = check_sequences(data,ctx.days,result.assignments)
        self.assertFalse(errors)
