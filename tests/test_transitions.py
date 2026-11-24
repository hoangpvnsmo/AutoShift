import unittest
from datetime import date, timedelta
from importlib.util import find_spec
from core.data.models import Employee, MonthData
from core.validation.transition_checks import transition_config, gap_minutes, forbidden_pairs, check_transitions

HAS_ORTOOLS = find_spec('ortools') is not None
if HAS_ORTOOLS:
    from core.model.model_builder import build_model
    from core.constraints.hard_constraints import add_shift_transition_constraints
    from core.optimization.solver import solve


def fixture(previous='OFF', hours=8):
    first = date(2026,12,1)
    e = Employee(1,'E1','SHIFT',True,(1,2,3))
    rules = {'max_shifts_per_day':1, 'max_consecutive_shift_days':5,
             'max_consecutive_night_shifts':3, 'min_rest_hours':hours,
             'forbidden_transitions':[[3,1]], 'shift_hours': {
                 '1':{'start':'22:00','end':'06:00','start_day_offset':-1},
                 '2':{'start':'06:00','end':'14:00','start_day_offset':0},
                 '3':{'start':'14:00','end':'22:00','start_day_offset':0}}}
    prev = {(1,first-timedelta(days=k)):previous if k==1 else 'OFF' for k in range(1,6)}
    return MonthData(2026,12,frozenset(),(e,),(),prev,rules)


class TransitionChecksTests(unittest.TestCase):
    def check(self, shifts, previous='OFF', hours=8):
        data = fixture(previous,hours)
        days = tuple(date(2026,12,1)+timedelta(days=k) for k in range(len(shifts)))
        return check_transitions(data,days,{(1,d):s for d,s in zip(days,shifts)})

    def test_matrix(self):
        cfg = transition_config(fixture())
        self.assertEqual([[gap_minutes(cfg,a,b)//60 for b in (1,2,3)] for a in (1,2,3)], [[16,24,32],[8,16,24],[0,8,16]])
        self.assertEqual(forbidden_pairs(cfg),{(3,1)})

    def test_12_hours(self):
        self.assertEqual(forbidden_pairs(transition_config(fixture(hours=12))),{(2,1),(3,1),(3,2)})

    def test_explicit_independent_of_hours(self):
        self.assertEqual(forbidden_pairs(transition_config(fixture(hours=0))),{(3,1)})

    def test_boundary_rejected(self):
        errors, stats = self.check([1],previous='3')
        self.assertEqual(len(errors),2)
        self.assertEqual(stats[1],0)

    def test_exact_min_allowed(self):
        errors, stats = self.check([1],previous='2')
        self.assertFalse(errors)
        self.assertEqual(stats[1],480)

    def test_off_between(self):
        errors,stats = self.check([0,1],previous='3')
        self.assertFalse(errors)
        self.assertEqual(stats[1],1440)

    def test_no_two_shifts(self):
        self.assertEqual(self.check([0,1])[1][1],None)

    def test_missing_history(self):
        data = fixture()
        data.previous.clear()
        with self.assertRaisesRegex(ValueError,'Previous: thiếu'):
            check_transitions(data,(date(2026,12,1),),{(1,date(2026,12,1)):0})

    def test_missing_config(self):
        data = fixture()
        data.rules['shift_hours'] = None
        with self.assertRaisesRegex(ValueError,'shift_hours'):
            transition_config(data)

    def test_bad_clock_or_offset(self):
        for key,value in [('start','24:00'),('start_day_offset',0)]:
            with self.subTest(key=key):
                data = fixture()
                data.rules['shift_hours']['1'][key]=value
                with self.assertRaises(ValueError):
                    transition_config(data)

    def test_alternative_midnight_schedule(self):
        data = fixture()
        data.rules['shift_hours'] = {str(s): {'start':a,'end':b,'start_day_offset':0} for s,a,b in [(1,'00:00','08:00'),(2,'08:00','16:00'),(3,'16:00','00:00')]}
        self.assertEqual(forbidden_pairs(transition_config(data)),{(3,1)})

    def test_history_across_day_off_when_needed(self):
        data = fixture(hours=24)
        data.rules['shift_hours']['3']['end'] = '23:00'
        cfg = transition_config(data)
        self.assertEqual(cfg.lookback,2)
        self.assertIn((3,1),forbidden_pairs(cfg,2))
        data.previous[1,date(2026,11,29)] = '3'
        d = date(2026,12,1)
        errors, stats = check_transitions(data,(d,),{(1,d):1})
        self.assertTrue(any('B04' in s for s in errors))
        self.assertEqual(stats[1],1380)


@unittest.skipUnless(HAS_ORTOOLS,'Thiếu OR-Tools')
class TransitionSolverTests(unittest.TestCase):
    def solve_fixed(self, shifts, previous='OFF', hours=8, data=None):
        data = data or fixture(previous,hours)
        ctx = build_model(data)
        add_shift_transition_constraints(ctx,data)
        for d,s in zip(ctx.days,shifts):
            ctx.model.add((ctx.x[1,d,s] if s else ctx.worked[1,d]) == int(bool(s)))
        return solve(ctx,time_limit=10).status

    def test_b02(self):
        self.assertEqual(self.solve_fixed([3,1],hours=0),'INFEASIBLE')

    def test_boundary(self):
        self.assertEqual(self.solve_fixed([1],previous='3'),'INFEASIBLE')

    def test_8_hours_allowed(self):
        self.assertIn(self.solve_fixed([3,2,1]),('OPTIMAL','FEASIBLE'))

    def test_12_hours_forbidden(self):
        self.assertEqual(self.solve_fixed([2,1],hours=12),'INFEASIBLE')

    def test_off_allowed(self):
        self.assertIn(self.solve_fixed([3,0,1]),('OPTIMAL','FEASIBLE'))

    def test_two_day_gap(self):
        data = fixture(hours=24)
        data.rules['shift_hours']['3']['end']='23:00'
        data.previous[1,date(2026,11,29)]='3'
        self.assertEqual(self.solve_fixed([1],data=data),'INFEASIBLE')
