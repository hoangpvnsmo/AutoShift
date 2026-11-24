import unittest
from dataclasses import replace
from datetime import date
from itertools import product
from importlib.util import find_spec
from core.data.models import Employee, MonthData
from core.data.calendar_utils import month_days
from core.objectives.day_balance_checks import day_sets, day_balance_rows
from core.objectives.day_balance_v2 import distribution_score, day_score_v2, day_bound

HAS_ORTOOLS=find_spec('ortools') is not None

def fixture(n=16, holidays=(), allow_holiday=False):
    return MonthData(2026,12,frozenset(holidays),tuple(Employee(i,str(i),'SHIFT',True,(1,2,3),23) for i in range(1,n+1))+
        (Employee(n+1,'A','ADMIN',False,(2,3)),),(),{},
        {'max_shifts_per_day':1,'min_previous_day_staff':1,'admin_support':{'enabled':True,'allowed_shifts':[2,3],'allow_weekend':False,'allow_holiday':allow_holiday,'max_shifts_per_person':None}})

def demand(days): return {(d,s):3 for d in days for s in (1,2,3)}

class BoundTests(unittest.TestCase):
    def test_sample_consecutive(self):
        h=(date(2026,12,25),date(2026,12,26)); b=day_bound(fixture(holidays=h),h,demand(h),0)
        self.assertEqual((b.lower,b.overlap,b.support_max),((2,84),3,15))
        self.assertEqual(b.ideal_values,(0,)+(1,)*12+(2,)*3)
    def test_old_and_new(self):
        self.assertEqual(distribution_score([0]*3+[1]*8+[2]*5),(2,140))
        self.assertEqual(distribution_score([0]+[1]*12+[2]*3),(2,84))
    def test_nonconsecutive(self):
        h=(date(2026,12,24),date(2026,12,26)); b=day_bound(fixture(holidays=h),h,demand(h),0)
        self.assertEqual((b.lower,b.overlap,b.support_max),((1,56),0,16))
    def test_weekend(self):
        data=fixture(); _,w=day_sets(data,month_days(2026,12)); b=day_bound(data,w,demand(w),10)
        self.assertEqual((b.lower,b.overlap),((1,128),12))
    def test_overlap_holiday_weekend(self):
        data=fixture(holidays=(date(2026,12,25),date(2026,12,26)))
        h,w=day_sets(data,month_days(2026,12)); b=day_bound(data,w,demand(w),0)
        self.assertEqual((len(h),len(w),b.lower),(2,7,(1,30)))
    def test_variable_admin(self):
        h=(date(2026,12,25),); b=day_bound(fixture(8,h,True),h,demand(h),1)
        self.assertEqual((b.total_min,b.total_max,b.lower,b.overlap),(8,9,(0,0),0)); self.assertIsNone(b.ideal_values)
    def test_admin_allowed_but_not_used(self):
        h=(date(2026,12,25),date(2026,12,26)); b=day_bound(fixture(16,h,True),h,demand(h),0)
        self.assertEqual(b.lower,(2,84))
    def test_no_days(self): self.assertEqual(day_bound(fixture(),(),{},0).lower,(0,0))
    def test_single(self):
        d=date(2026,12,25); b=day_bound(fixture(1,(d,)),(d,),{(d,1):1},0); self.assertEqual(b.lower,(0,0))
    def test_support_relaxation_exhaustive(self):
        # All distributions on 1..4 people, counts 0..4. Compare independently
        # with the balanced distribution on at most u occupied positions.
        for n in range(1,5):
            for vals in product(range(5),repeat=n):
                t=sum(vals); actual=distribution_score(vals)
                for u in range(max(1,sum(v>0 for v in vals)),n+1):
                    q,r=divmod(t,u); low=distribution_score([0]*(n-u)+[q]*(u-r)+[q+1]*r)
                    self.assertTrue(all(a>=b for a,b in zip(actual,low)),(vals,u,actual,low))

@unittest.skipUnless(HAS_ORTOOLS,'Thiếu OR-Tools')
class SolverTests(unittest.TestCase):
    def make(self, impossible=False):
        from core.model.model_builder import build_model
        from core.objectives.day_balance_v2 import add_day_balance_v2
        from core.optimization.solver import solve
        dates=(date(2026,12,25),date(2026,12,27)); data=fixture(2,dates)
        ctx=build_model(data); m=ctx.model
        q,terms,obj=add_day_balance_v2(ctx,data,dates,'holiday')
        for d in dates: m.add(ctx.worked[1,d]+ctx.worked[2,d]==1)
        if impossible:
            for d in dates: m.add(ctx.worked[1,d]==1)
        m.maximize(obj); base=solve(ctx,5)
        self.assertIsNotNone(base.assignments)
        score=lambda a:day_score_v2(day_balance_rows(data,ctx.days,a),'holiday')
        b=day_bound(data,dates,{(d,1):1 for d in dates},0)
        return ctx,q,terms,obj,base,score,b
    def test_trial_and_lock(self):
        from core.optimization.objective_day_v2 import refine_day
        from core.optimization.solver import solve
        ctx,q,terms,obj,base,score,b=self.make()
        r,l=refine_day(ctx,base,score,q,terms,obj,b,10,3,lambda a:None)
        self.assertEqual(l.after,(0,0)); self.assertEqual(l.status,'PROVEN_BOUND')
        ctx.model.maximize(obj); self.assertEqual(score(solve(ctx,5).assignments),(0,0))
    def test_infeasible_trial_no_leak(self):
        from core.optimization.objective_day_v2 import refine_day
        ctx,q,terms,obj,base,score,b=self.make(True)
        r,l=refine_day(ctx,base,score,q,terms,obj,b,10,3,lambda a:None)
        self.assertEqual(l.after,(2,4)); self.assertEqual(l.status,'OPTIMAL'); self.assertIn('INFEASIBLE',l.note)
    def test_timeout_keeps_incumbent(self):
        from unittest.mock import patch
        from core.optimization.objective_day_v2 import refine_day
        from core.optimization.solver import SolveResult
        ctx,q,terms,obj,base,score,b=self.make()
        with patch('core.optimization.objective_day_v2.ideal_trial',return_value=SolveResult('UNKNOWN',None,.1)), patch('core.optimization.objective_days.solve_to_bound',return_value=SolveResult('UNKNOWN',None,.1)):
            r,l=refine_day(ctx,base,score,q,terms,obj,b,10,3,lambda a:None)
        self.assertIs(r,base); self.assertEqual(l.after,l.before); self.assertEqual(l.status,'UNKNOWN')
    def test_integration(self):
        from pathlib import Path
        from core.data.data_loader import load_data
        from core.model.engine_model import build_stage3
        from core.optimization.objective_days_v2 import solve_quality431
        from core.validation.credit_checks import check_staffing
        from core.validation.admin_checks import check_admin_credits
        from core.validation.sequence_checks import check_sequences
        from core.validation.transition_checks import check_transitions
        root=Path(__file__).resolve().parents[1]; data=load_data(root/'tests/fixtures/input/input.xlsx',root/'tests/fixtures/config/rules.json')
        if data.rules.get('shift_hours') is None:
            data.rules['shift_hours']={str(s):{'start':a,'end':b,'start_day_offset':o} for s,a,b,o in [(1,'22:00','06:00',-1),(2,'06:00','14:00',0),(3,'14:00','22:00',0)]}
        data=replace(data,holidays=data.holidays|frozenset((date(data.year,data.month,25),date(data.year,data.month,26))))
        ctx,dem,_,credit=build_stage3(data); q=solve_quality431(ctx,data,credit,dem,30,60,30,30,30,10)
        a=q.selected.assignments; self.assertIsNotNone(a)
        self.assertFalse(check_staffing(data,ctx.days,a)); self.assertFalse(check_admin_credits(data,ctx.days,a)[0]); self.assertFalse(check_sequences(data,ctx.days,a)[0]); self.assertFalse(check_transitions(data,ctx.days,a)[0])
        for l in (q.e01,q.e02,q.holiday,q.weekend): self.assertLessEqual(l.after,l.before)
        rows=day_balance_rows(data,ctx.days,a); self.assertEqual(day_score_v2(rows,'holiday'),q.holiday.after); self.assertEqual(day_score_v2(rows,'weekend'),q.weekend.after)

if __name__=='__main__': unittest.main()
