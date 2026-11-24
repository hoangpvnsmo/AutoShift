import unittest
from datetime import date, timedelta
from importlib.util import find_spec
from core.data.models import Employee, MonthData

HAS_ORTOOLS = find_spec('ortools') is not None
if HAS_ORTOOLS:
    from core.model.model_builder import build_model
    from core.constraints.hard_constraints import (
        add_staffing_constraints, add_shift_eligibility, disable_admin_for_trial,
        add_previous_day_continuity,
    )
    from core.optimization.solver import solve


@unittest.skipUnless(HAS_ORTOOLS, 'Thiếu OR-Tools; cần cài requirements.txt.')
class ContinuityTests(unittest.TestCase):
    def setUp(self):
        employees = tuple(Employee(i, f'E{i}', 'SHIFT', i <= 6, (1, 2, 3))
                          for i in range(1, 17))
        employees += (Employee(17, 'ADMIN', 'ADMIN', False, (2, 3)),)
        self.prev = date(2026, 11, 30)
        self.prior_ids = {1, 2, 3, 7, 8, 9, 10, 11, 12}
        history = {(i, self.prev): '1' if i in self.prior_ids else 'OFF'
                   for i in range(1, 18)}
        history.update({(4, self.prev): 'CT', (13, self.prev): 'DT',
                        (14, self.prev): 'BKD', (17, self.prev): 'T'})
        rules = {'max_shifts_per_day': 1, 'min_leaders_per_shift': 1,
                 'min_previous_day_staff': 1,
                 'staff_per_shift': {str(s): {'min': 3, 'preferred': 4, 'max': 4}
                                     for s in (1, 2, 3)}}
        self.data = MonthData(2026, 12, frozenset(), employees, (), history, rules)
        self.ctx = build_model(self.data)
        self.d1, self.d2 = self.ctx.days[:2]

    def apply_base(self):
        add_staffing_constraints(self.ctx, self.data)
        add_shift_eligibility(self.ctx, self.data)
        disable_admin_for_trial(self.ctx, self.data)

    def apply_rules(self):
        self.apply_base()
        add_previous_day_continuity(self.ctx, self.data)

    def fix_shift(self, day, shift, ids):
        for i in self.ctx.employee_ids:
            self.ctx.model.add(self.ctx.x[i, day, shift] == int(i in ids))

    def fix_first_day(self):
        self.fix_shift(self.d1, 1, {1, 7, 8})
        self.fix_shift(self.d1, 2, {2, 9, 10})
        self.fix_shift(self.d1, 3, {3, 11, 12})

    def assert_infeasible(self):
        result = solve(self.ctx, time_limit=30)
        self.assertEqual(result.status, 'INFEASIBLE')
        self.assertIsNone(result.assignments)

    def assert_feasible(self):
        result = solve(self.ctx, time_limit=30)
        self.assertIn(result.status, ('OPTIMAL', 'FEASIBLE'))
        self.assertIsNotNone(result.assignments)
        return result.assignments

    def test_valid_month_including_boundary(self):
        self.apply_rules()
        assignments = self.assert_feasible()
        prev = self.prior_ids
        for d in self.ctx.days:
            for s in self.ctx.shifts:
                ids = {i for i in self.ctx.employee_ids if assignments[i, d] == s}
                self.assertGreaterEqual(len(ids & prev), 1)
            prev = {i for i in self.ctx.employee_ids if assignments[i, d] in (1, 2, 3)}

    def test_first_day_non_shift_history_not_counted(self):
        self.apply_rules()
        # CT, DT, BKD đều không được tính là trực ngày trước.
        self.fix_shift(self.d1, 1, {4, 13, 14})
        self.assert_infeasible()

    def test_first_day_no_prior_staff(self):
        for i in self.ctx.employee_ids:
            self.data.previous[i, self.prev] = 'OFF'
        self.apply_rules()
        self.assert_infeasible()

    def test_second_day_disjoint(self):
        self.apply_rules()
        self.fix_first_day()
        self.fix_shift(self.d2, 1, {4, 13, 14})
        self.assert_infeasible()

    def test_cross_shift_is_allowed(self):
        self.apply_rules()
        self.fix_first_day()
        # ID 2 là người duy nhất nối tiếp: ca 2 hôm trước, ca 1 hôm sau.
        self.fix_shift(self.d2, 1, {2, 13, 14})
        self.assert_feasible()

    def test_configurable_minimum(self):
        self.data.rules['min_previous_day_staff'] = 2
        self.apply_rules()
        self.fix_shift(self.d1, 1, {1, 13, 14})
        self.assert_infeasible()

    def test_missing_history_rejected(self):
        del self.data.previous[1, self.prev]
        with self.assertRaisesRegex(ValueError, 'Previous: thiếu'):
            self.apply_rules()

    def test_invalid_minimum_rejected(self):
        self.data.rules['min_previous_day_staff'] = 5
        with self.assertRaises(ValueError):
            self.apply_rules()

    def test_disjoint_allowed_without_A07(self):
        # Đối chứng: cùng lịch không nối tiếp, nhưng chưa áp dụng A07.
        self.apply_base()
        self.fix_first_day()
        self.fix_shift(self.d2, 1, {4, 13, 14})
        self.assert_feasible()
