import unittest
from importlib.util import find_spec
from pathlib import Path
from core.data.data_loader import load_data

HAS_ORTOOLS = find_spec('ortools') is not None
if HAS_ORTOOLS:
    from core.model.model_builder import build_model
    from core.optimization.solver import solve

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(HAS_ORTOOLS, 'Thiếu OR-Tools; cài requirements.txt để chạy kiểm thử solver.')
class ModelBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_data(ROOT / 'tests/fixtures/input/input.xlsx', ROOT / 'tests/fixtures/config/rules.json')

    def test_variable_counts(self):
        ctx = build_model(self.data)
        self.assertEqual(len(ctx.x), len(ctx.employee_ids)*len(ctx.days)*3)
        self.assertEqual(len(ctx.worked), len(ctx.employee_ids)*len(ctx.days))

    def test_one_shift(self):
        ctx = build_model(self.data)
        i, d = ctx.employee_ids[0], ctx.days[0]
        ctx.model.add(ctx.x[i, d, 2] == 1)
        result = solve(ctx)
        self.assertIn(result.status, ('OPTIMAL', 'FEASIBLE'))
        self.assertEqual(result.assignments[i, d], 2)

    def test_two_shifts_forbidden(self):
        for a, b in [(1, 2), (1, 3), (2, 3)]:
            with self.subTest(a=a, b=b):
                ctx = build_model(self.data)
                i, d = ctx.employee_ids[0], ctx.days[0]
                ctx.model.add(ctx.x[i, d, a] == 1)
                ctx.model.add(ctx.x[i, d, b] == 1)
                result = solve(ctx)
                self.assertEqual(result.status, 'INFEASIBLE')
                self.assertIsNone(result.assignments)

    def test_no_shift(self):
        ctx = build_model(self.data)
        i, d = ctx.employee_ids[0], ctx.days[0]
        ctx.model.add(ctx.worked[i, d] == 0)
        result = solve(ctx)
        self.assertIn(result.status, ('OPTIMAL', 'FEASIBLE'))
        self.assertEqual(result.assignments[i, d], 0)
