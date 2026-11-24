import unittest
from importlib.util import find_spec
from core.data.models import Employee, MonthData

HAS_ORTOOLS = find_spec('ortools') is not None
if HAS_ORTOOLS:
    from core.model.model_builder import build_model
    from core.constraints.hard_constraints import (
        add_staffing_constraints, add_shift_eligibility, disable_admin_for_trial,
    )
    from core.optimization.solver import solve


@unittest.skipUnless(HAS_ORTOOLS, 'Thiếu OR-Tools; cần cài requirements.txt.')
class StaffingTests(unittest.TestCase):
    def setUp(self):
        # Dữ liệu riêng cho test, không phụ thuộc Excel người sử dụng đã chỉnh sửa.
        employees = tuple(Employee(i, f'E{i}', 'SHIFT', i <= 6,
                                   (2, 3) if i == 16 else (1, 2, 3))
                          for i in range(1, 17))
        employees += (Employee(17, 'ADMIN', 'ADMIN', False, (2, 3)),)
        rules = {'max_shifts_per_day': 1, 'min_leaders_per_shift': 1,
                 'staff_per_shift': {str(s): {'min': 3, 'preferred': 4, 'max': 4}
                                     for s in (1, 2, 3)}}
        self.data = MonthData(2026, 12, frozenset(), employees, (), {}, rules)
        self.ctx = build_model(self.data)
        self.d = self.ctx.days[0]

    def apply_rules(self, admin_off=True):
        add_staffing_constraints(self.ctx, self.data)
        add_shift_eligibility(self.ctx, self.data)
        if admin_off:
            disable_admin_for_trial(self.ctx, self.data)

    def assert_infeasible(self):
        result = solve(self.ctx)
        self.assertEqual(result.status, 'INFEASIBLE')
        self.assertIsNone(result.assignments)

    def test_valid_month(self):
        self.apply_rules()
        result = solve(self.ctx)
        self.assertIn(result.status, ('OPTIMAL', 'FEASIBLE'))
        for d in self.ctx.days:
            for s in self.ctx.shifts:
                ids = [i for i in self.ctx.employee_ids if result.assignments[i, d] == s]
                self.assertTrue(3 <= len(ids) <= 4)
                self.assertTrue(any(i <= 6 for i in ids))
                self.assertNotIn(17, ids)
                if s == 1:
                    self.assertNotIn(16, ids)

    def test_understaffed(self):
        self.apply_rules()
        for i in self.ctx.employee_ids:
            if i not in (1, 7):
                self.ctx.model.add(self.ctx.x[i, self.d, 1] == 0)
        self.assert_infeasible()

    def test_overstaffed(self):
        self.apply_rules()
        for i in (1, 7, 8, 9, 10):
            self.ctx.model.add(self.ctx.x[i, self.d, 1] == 1)
        self.assert_infeasible()

    def test_no_leader(self):
        self.apply_rules()
        for e in self.data.employees:
            if e.is_leader:
                self.ctx.model.add(self.ctx.x[e.id, self.d, 1] == 0)
        self.assert_infeasible()

    def test_ineligible_shift(self):
        self.apply_rules()
        self.ctx.model.add(self.ctx.x[16, self.d, 1] == 1)
        self.assert_infeasible()

    def test_admin_disabled_for_trial(self):
        self.apply_rules()
        self.ctx.model.add(self.ctx.x[17, self.d, 2] == 1)
        self.assert_infeasible()

    def test_configurable_limits(self):
        self.data.rules['staff_per_shift']['2'] = {'min': 4, 'preferred': 4, 'max': 5}
        self.apply_rules()
        for i in (1, 7, 8, 9, 10):
            self.ctx.model.add(self.ctx.x[i, self.d, 2] == 1)
        result = solve(self.ctx)
        self.assertIn(result.status, ('OPTIMAL', 'FEASIBLE'))
        self.assertEqual(sum(result.assignments[i, self.d] == 2 for i in self.ctx.employee_ids), 5)

    def test_invalid_limits(self):
        self.data.rules['staff_per_shift']['1']['max'] = 2
        with self.assertRaises(ValueError):
            self.apply_rules()
