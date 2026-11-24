import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from importlib.util import find_spec
from core.data.models import Employee, MonthData
from core.validation.admin_checks import AdminCreditRow, check_admin_credits
from core.objectives.quality_checks import ShiftBalanceRow, shift_balance_rows, shift_balance_score, workforce_score

HAS_ORTOOLS = find_spec('ortools') is not None
if HAS_ORTOOLS:
    from core.model.model_builder import build_model
    from core.constraints.admin_constraints import add_workforce_objective, add_admin_constraints, add_all_request_constraints
    from core.optimization.objective import solve_shift_quality
    from core.objectives.soft_constraints import add_shift_balance
    from core.optimization.solver import solve


def fixture():
    people=(Employee(1,'S1','SHIFT',True,(1,2,3),23),Employee(2,'S2','SHIFT',False,(1,2,3),23),
            Employee(3,'A1','ADMIN',False,(2,3)))
    rules={'max_shifts_per_day':1,'work_credit':{'1':1,'2':1,'3':1,'CT':1,'DT':1,'BKD':0,'OFF':0,'T':1},
           'admin_support':{'enabled':True,'allowed_shifts':[2,3],'allow_weekend':False,'allow_holiday':False,'max_shifts_per_person':None}}
    return MonthData(2026,12,frozenset(),people,(),{},rules)


class QualityChecksTests(unittest.TestCase):
    def test_equal(self):
        self.assertEqual(ShiftBalanceRow(1,(6,6,6),(1,2,3)).gap,0)

    def test_unequal(self):
        self.assertEqual(ShiftBalanceRow(14,(13,1,3),(1,2,3)).gap,12)

    def test_only_allowed(self):
        self.assertEqual(ShiftBalanceRow(1,(0,6,5),(2,3)).gap,1)

    def test_one_or_no_allowed(self):
        for allowed in [(2,),()]:
            self.assertEqual(ShiftBalanceRow(1,(0,7,0),allowed).gap,0)

    def test_lex_score(self):
        rows=[ShiftBalanceRow(1,(2,4,5),(1,2,3)),ShiftBalanceRow(2,(6,6,5),(1,2,3))]
        self.assertEqual(shift_balance_score(rows),(3,4))
        self.assertLess((2,10),(3,4))

    def test_only_shift_not_admin(self):
        data=fixture(); days=(date(2026,12,1),date(2026,12,2))
        a={(e.id,d):0 for e in data.employees for d in days}
        a[1,days[0]]=1; a[1,days[1]]=3; a[3,days[0]]=2
        rows=shift_balance_rows(data,days,a)
        self.assertEqual([r.id for r in rows],[1,2]); self.assertEqual(rows[0].counts,(1,0,1))

    def test_workforce_score(self):
        def row(i,group,target,n,m,a,ct=0,t=0):
            return AdminCreditRow(i,group,target,n,m,a,ct,0,0,t,0,0)
        rows=[row(1,'SHIFT',3,2,1,0,ct=1),row(2,'SHIFT',3,0,2,1),
              row(3,'ADMIN',23,0,1,0,t=22),row(4,'ADMIN',23,0,0,2,t=21)]
        self.assertEqual(workforce_score(rows),(1,3,1,2,2))

    def test_workforce_no_admin(self):
        r=AdminCreditRow(1,'SHIFT',23,5,6,6,0,0,0,0,14,0)
        self.assertEqual(workforce_score([r]),(0,0,0,0,0))


@unittest.skipUnless(HAS_ORTOOLS,'Thiếu OR-Tools')
class QualitySolverTests(unittest.TestCase):
    def test_exact_balance(self):
        data=fixture(); ctx=build_model(data)
        add_admin_constraints(ctx,data)
        for e in data.employees:
            for s in ctx.shifts:
                if s not in e.allowed_shifts:
                    for d in ctx.days: ctx.model.add(ctx.x[e.id,d,s]==0)
        for i in (1,2): ctx.model.add(sum(ctx.worked[i,d] for d in ctx.days)==6)
        quality=add_shift_balance(ctx,data); ctx.model.minimize(quality.objective)
        r=solve(ctx); self.assertEqual(r.status,'OPTIMAL')
        self.assertEqual(shift_balance_score(shift_balance_rows(data,ctx.days,r.assignments)),(0,0))

    def test_unavoidable_gap_is_soft(self):
        data=fixture(); ctx=build_model(data)
        for d in ctx.days[:5]: ctx.model.add(ctx.x[1,d,1]==1)
        ctx.model.add(sum(ctx.worked[1,d] for d in ctx.days)==5)
        quality=add_shift_balance(ctx,data); ctx.model.minimize(quality.objective)
        r=solve(ctx); self.assertEqual(r.status,'OPTIMAL')
        self.assertEqual(shift_balance_score(shift_balance_rows(data,ctx.days,r.assignments)),(5,5))

    def small_model(self,data):
        ctx=build_model(data); add_admin_constraints(ctx,data); add_all_request_constraints(ctx,data)
        for k,d in enumerate(ctx.days):
            for s in ctx.shifts:
                ctx.model.add(sum(ctx.x[i,d,s] for i in ctx.employee_ids)==int(k<6 and s==k%3+1))
        credit=add_workforce_objective(ctx,data)
        return ctx,credit

    def test_priority_no_extra_admin(self):
        data=fixture(); ctx,credit=self.small_model(data)
        q=solve_shift_quality(ctx,data,credit,10,10)
        self.assertEqual(q.baseline.status,'OPTIMAL'); self.assertEqual(q.refinement.status,'OPTIMAL')
        self.assertEqual(q.workforce,(0,0,0,0,0)); self.assertEqual(q.after,(0,0))
        self.assertLessEqual(q.after,q.before)

    def test_disabled(self):
        data=fixture(); data.rules['optimization']={'balance_shift_types':False}
        ctx,credit=self.small_model(data); q=solve_shift_quality(ctx,data,credit,10,10)
        self.assertIsNone(q.refinement); self.assertEqual(q.before,q.after)

    def test_timeout_fallback(self):
        from unittest.mock import patch
        from core.optimization.solver import SolveResult
        data=fixture(); ctx,credit=self.small_model(data)
        baseline=solve(ctx,time_limit=10); self.assertIsNotNone(baseline.assignments)
        with patch('core.optimization.objective.solve',side_effect=[baseline,SolveResult('UNKNOWN',None,0.01)]):
            q=solve_shift_quality(ctx,data,credit,10,10)
        self.assertIs(q.selected,baseline); self.assertEqual(q.before,q.after)

    def test_sample_both_scenarios(self):
        from core.data.data_loader import load_data
        from core.model.engine_model import build_stage3
        from core.validation.credit_checks import check_staffing
        from core.validation.sequence_checks import check_sequences
        from core.validation.transition_checks import check_transitions
        root=Path(__file__).resolve().parents[1]
        source=load_data(root/'tests/fixtures/input/input.xlsx',root/'tests/fixtures/config/rules.json')
        if source.rules.get('shift_hours') is None:
            source.rules['shift_hours']={str(s):{'start':a,'end':b,'start_day_offset':o} for s,a,b,o in [(1,'22:00','06:00',-1),(2,'06:00','14:00',0),(3,'14:00','22:00',0)]}
        # Integration uses current input, not hard-coded totals from the original sample.
        for demo in (None,17):
            with self.subTest(demo=demo):
                data=source if demo is None else replace(source,employees=tuple(replace(e,target_credit=demo) if e.group=='SHIFT' else e for e in source.employees))
                ctx,demand,_,credit=build_stage3(data)
                q=solve_shift_quality(ctx,data,credit,30,60)
                a=q.selected.assignments; self.assertIsNotNone(a,q.selected.status)
                err,_,rows=check_admin_credits(data,ctx.days,a)
                self.assertFalse(err); self.assertFalse(check_staffing(data,ctx.days,a))
                self.assertFalse(check_sequences(data,ctx.days,a)[0]); self.assertFalse(check_transitions(data,ctx.days,a)[0])
                self.assertEqual(sum(r.shifts for r in rows),sum(demand.values()))
                self.assertEqual(workforce_score(rows),q.workforce); self.assertLessEqual(q.after,q.before)

if __name__=='__main__': unittest.main()
