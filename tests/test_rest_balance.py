import unittest
from dataclasses import replace
from datetime import date,timedelta
from importlib.util import find_spec
from core.data.models import Employee,MonthData,Request
from core.data.calendar_utils import month_days
from core.objectives.rest_checks import rest_windows,rest_rows,rest_scores,weekend_lower

HAS_ORTOOLS=find_spec('ortools') is not None

def fixture(year=2026,month=12,n=2):
    days=month_days(year,month); first=days[0]
    employees=tuple(Employee(i,str(i),'SHIFT',True,(1,2,3)) for i in range(1,n+1))
    history={(i,first-timedelta(days=j)):'2' for i in range(1,n+1) for j in range(1,6)}
    rules={'max_shifts_per_day':1,'max_consecutive_night_shifts':3,'max_consecutive_shift_days':5,
           'min_rest_hours':8,'forbidden_transitions':[[3,1]],
           'shift_hours':{'1':{'start':'22:00','end':'06:00','start_day_offset':-1},
                          '2':{'start':'06:00','end':'14:00','start_day_offset':0},
                          '3':{'start':'14:00','end':'22:00','start_day_offset':0}}}
    data=MonthData(year,month,frozenset(),employees,(),history,rules)
    a={(e.id,d):2 for e in employees for d in days}
    return data,days,a

class RestTests(unittest.TestCase):
    def test_isolated_vs_pair(self):
        d,days,a=fixture(); a[1,days[3]]=0; a[1,days[5]]=0
        r=rest_rows(d,days,a)[0]; self.assertEqual((r.singles,r.blocks),(2,0))
        a[1,days[4]]=0; a[1,days[5]]=2
        r=rest_rows(d,days,a)[0]; self.assertEqual((r.singles,r.blocks),(0,1))
    def test_non_shift_not_rest(self):
        d,days,a=fixture(); d=replace(d,requests=(Request(1,days[3],'CT'),Request(1,days[4],'DT'),Request(1,days[5],'BKD')))
        for day in days[3:6]: a[1,day]=0
        self.assertEqual(rest_rows(d,days,a)[0].off,0)
    def test_night_monday(self):
        d,days,a=fixture(); sat=date(2026,12,5); sun=date(2026,12,6); mon=date(2026,12,7)
        a[1,sat]=a[1,sun]=0
        self.assertEqual(rest_rows(d,days,a)[0].full_weekends,1)
        a[1,mon]=1
        self.assertEqual(rest_rows(d,days,a)[0].full_weekends,0)
    def test_history_block(self):
        d,days,a=fixture(); d.previous[1,days[0]-timedelta(days=1)]='OFF'; a[1,days[0]]=0
        r=rest_rows(d,days,a)[0]; self.assertEqual((r.singles,r.blocks),(0,1))
    def test_long_extra(self):
        d,days,a=fixture();
        for day in days[4:9]: a[1,day]=0
        r=rest_rows(d,days,a)[0]; self.assertEqual((r.long_extra,r.blocks),(2,1))
    def test_last_day_not_invented(self):
        d,days,a=fixture(); a[1,days[-1]]=0
        self.assertEqual(rest_rows(d,days,a)[0].singles,0)
    def test_month_ends_sunday(self):
        d,days,a=fixture(2027,1); w=rest_windows(d,days)
        self.assertIn((date(2027,1,30),date(2027,1,31)),w.pending_weekends)
        self.assertEqual(len(w.weekends),4)
    def test_sunday_first(self):
        d,days,a=fixture(2026,11); first=days[0]; d.previous[1,first-timedelta(days=1)]='OFF'; a[1,first]=0
        self.assertEqual(rest_rows(d,days,a)[0].full_weekends,1)
    def test_holiday_weekend_still_rest_weekend(self):
        d,days,a=fixture(); sat=date(2026,12,5); sun=date(2026,12,6); d=replace(d,holidays=frozenset((sat,)))
        a[1,sat]=a[1,sun]=0
        self.assertEqual(rest_rows(d,days,a)[0].full_weekends,1)
    def test_bound(self):
        d,days,a=fixture(n=16); w=rest_windows(d,days); demand={(day,s):3 for day in days for s in (1,2,3)}
        self.assertEqual(weekend_lower(d,w,demand,0),(0,36,0,0))
    def test_scores(self):
        d,days,a=fixture(); w=rest_windows(d,days); a[1,date(2026,12,5)]=a[1,date(2026,12,6)]=0
        self.assertEqual(rest_scores(rest_rows(d,days,a),len(w.weekends)),((0,0,0),(1,7,1,1)))

@unittest.skipUnless(HAS_ORTOOLS,'Thiếu OR-Tools')
class SolverTests(unittest.TestCase):
    def test_model_terms_match_independent(self):
        from core.model.model_builder import build_model
        from core.objectives.rest_constraints import add_rest_balance
        from ortools.sat.python import cp_model
        d,days,a=fixture(); d=replace(d,requests=(Request(1,days[10],'CT'),)); a[1,days[10]]=0
        for day in days[4:6]: a[1,day]=0
        a[1,days[6]]=1; a[2,days[10]]=0
        ctx=build_model(d); r=add_rest_balance(ctx,d)
        for (i,day,s),v in ctx.x.items(): ctx.model.add(v==int(a[i,day]==s))
        solver=cp_model.CpSolver(); self.assertIn(solver.solve(ctx.model),(cp_model.OPTIMAL,cp_model.FEASIBLE))
        expected=rest_scores(rest_rows(d,days,a),len(r.windows.weekends))
        self.assertEqual(tuple(tuple(solver.value(v) for v in terms) for terms in (r.terms_a,r.terms_b)),expected)
    def test_prefers_pair(self):
        from core.model.model_builder import build_model
        from core.objectives.rest_constraints import add_rest_balance
        from core.optimization.solver import solve
        d,days,a=fixture(n=1); ctx=build_model(d); r=add_rest_balance(ctx,d)
        for day in days:
            if day not in days[3:6]: ctx.model.add(ctx.x[1,day,2]==1)
        ctx.model.add(sum(ctx.worked[1,day] for day in days[3:6])==1)
        ctx.model.minimize(r.obj_a); result=solve(ctx,10)
        self.assertEqual(result.status,'OPTIMAL')
        self.assertEqual(rest_rows(d,days,result.assignments)[0].singles,0)
    def test_full_weekend_avoids_monday_night(self):
        from core.model.model_builder import build_model
        from core.objectives.rest_constraints import add_rest_balance
        from core.optimization.solver import solve
        d,days,a=fixture(n=1); ctx=build_model(d); r=add_rest_balance(ctx,d)
        sat,sun,mon=map(date.fromisoformat,('2026-12-05','2026-12-06','2026-12-07'))
        for day in days:
            if day in (sat,sun): ctx.model.add(ctx.worked[1,day]==0)
            elif day==mon: ctx.model.add(ctx.worked[1,day]==1)
            else: ctx.model.add(ctx.x[1,day,2]==1)
        ctx.model.minimize(r.obj_b); result=solve(ctx,10)
        self.assertEqual(result.status,'OPTIMAL'); self.assertNotEqual(result.assignments[1,mon],1)
        self.assertEqual(rest_rows(d,days,result.assignments)[0].full_weekends,1)

if __name__=='__main__': unittest.main()
