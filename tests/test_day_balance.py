import unittest
from datetime import date
from dataclasses import replace
from importlib.util import find_spec
from core.data.models import Employee, MonthData, Request
from core.data.calendar_utils import month_days
from core.objectives.day_balance_checks import day_sets, day_balance_rows, day_score, day_lower_bound, e01_lower_bound

HAS_ORTOOLS=find_spec('ortools') is not None

def fixture(n=16):
    return MonthData(2026,12,frozenset(),tuple(Employee(i,str(i),'SHIFT',True,(1,2,3),23) for i in range(1,n+1))+
        (Employee(n+1,'A','ADMIN',False,(2,3)),),(),{},
        {'max_shifts_per_day':1,'admin_support':{'enabled':True,'allowed_shifts':[2,3],'allow_weekend':False,'allow_holiday':False,'max_shifts_per_person':None}})

def demand_for(days): return {(d,s):3 for d in days for s in (1,2,3)}

class DayTests(unittest.TestCase):
    def test_calendar(self):
        data=fixture(); days=month_days(2026,12); h,w=day_sets(data,days)
        self.assertEqual(len(h),0); self.assertEqual(len(w),8)
        self.assertEqual([d.day for d in w],[5,6,12,13,19,20,26,27])
    def test_overlap(self):
        data=replace(fixture(),holidays=frozenset((date(2026,12,25),date(2026,12,26))))
        h,w=day_sets(data,month_days(2026,12)); self.assertEqual((len(h),len(w)),(2,7)); self.assertFalse(set(h)&set(w))
    def test_count_shift_only_and_admin_excluded(self):
        data=replace(fixture(2),holidays=frozenset((date(2026,12,5),)))
        days=month_days(2026,12); a={(e.id,d):0 for e in data.employees for d in days}
        a[1,date(2026,12,5)]=1; a[1,date(2026,12,6)]=3
        a[3,date(2026,12,5)]=2; a[3,date(2026,12,6)]=2 # thống kê độc lập, không phải lịch hợp lệ D
        rows=day_balance_rows(data,days,a)
        self.assertEqual((rows[0].holiday,rows[0].weekend),(1,1)); self.assertEqual(day_score(rows,'holiday'),(1,)); self.assertEqual(day_score(rows,'weekend'),(1,))
    def test_weekend_bound(self):
        data=fixture(); days=month_days(2026,12); _,w=day_sets(data,days)
        self.assertEqual(day_lower_bound(data,w,demand_for(days),10),(1,))
    def test_holiday_bound_no_admin(self):
        data=replace(fixture(),holidays=frozenset((date(2026,12,25),date(2026,12,26))))
        days=month_days(2026,12); h,w=day_sets(data,days)
        self.assertEqual(day_lower_bound(data,h,demand_for(days),10),(1,))
        self.assertEqual(day_lower_bound(data,w,demand_for(days),10),(1,))
    def test_holiday_admin_variable_total(self):
        data=replace(fixture(8),holidays=frozenset((date(2026,12,25),)))
        data.rules['admin_support']['allow_holiday']=True
        h=tuple(data.holidays); dem=demand_for(h)
        self.assertEqual(day_lower_bound(data,h,dem,1),(0,)) # 8 or 9 SHIFT
        self.assertEqual(day_lower_bound(data,h,dem,0),(1,))
    def test_no_days(self):
        data=fixture(); self.assertEqual(day_lower_bound(data,(),{},10),(0,))
    def test_e01_sample(self):
        data=replace(fixture(),requests=tuple(Request(13,date(2026,12,d),c) for d,c in [(7,'CT'),(8,'CT'),(9,'DT')]))
        days=month_days(2026,12); dem=demand_for(days)
        self.assertEqual(e01_lower_bound(data,days,dem,(0,0,1,120,0)),(1,6))
        self.assertEqual(e01_lower_bound(data,days,dem,(0,10,0,0,0)),(1,16))
    def test_e01_mixed_rights_and_ct(self):
        data=fixture(2); data=replace(data,employees=(replace(data.employees[0],allowed_shifts=(1,2)),*data.employees[1:]),requests=(Request(1,date(2026,12,1),'CT'),))
        # 9 shifts + CT =10 credits, 5 each. Person 1:4 shifts divisible by 2, person 2:5 not divisible by 3.
        days=month_days(2026,12)
        self.assertEqual(e01_lower_bound(data,days,{(days[0],1):9},(0,0,0,0,0)),(1,1))
    def test_e01_choose_high_person(self):
        data=fixture(2); data=replace(data,employees=(replace(data.employees[0],allowed_shifts=(1,2)),*data.employees[1:]))
        days=month_days(2026,12)
        # 5 credits -> 2,3 can both divide; swapping cannot. DP must not use one arbitrary incumbent.
        self.assertEqual(e01_lower_bound(data,days,{(days[0],1):5},(0,0,1,2,0)),(0,0))
    def test_e01_wide_spread_safe(self):
        self.assertEqual(e01_lower_bound(fixture(),month_days(2026,12),{},(0,0,2,0,0)),(0,0))

@unittest.skipUnless(HAS_ORTOOLS,'Thiếu OR-Tools')
class DaySolverTests(unittest.TestCase):
    def test_holiday_priority_and_weekend_improve(self):
        from core.model.model_builder import build_model
        from core.objectives.day_balance_constraints import add_day_balance
        from core.optimization.objective_days import refine_bounded
        from core.optimization.solver import solve
        data=replace(fixture(2),holidays=frozenset((date(2026,12,5),date(2026,12,6))))
        ctx=build_model(data); m=ctx.model; h,w=day_sets(data,ctx.days)
        hq=add_day_balance(ctx,data,h,'holiday'); wq=add_day_balance(ctx,data,w,'weekend')
        for d in h+w: m.add(ctx.worked[1,d]+ctx.worked[2,d]==1)
        # Chọn lịch nền cực lệch; sau đó bỏ ràng buộc theo người bằng cách chỉ đổi mục tiêu.
        m.maximize(hq.gap+wq.gap); base=solve(ctx,5)
        self.assertEqual(base.status,'OPTIMAL')
        def sh(a):return day_score(day_balance_rows(data,ctx.days,a),'holiday')
        def sw(a):return day_score(day_balance_rows(data,ctx.days,a),'weekend')
        r,l=refine_bounded(ctx,base,sh,(hq.gap,),hq.gap,(0,),5,lambda a:None)
        self.assertEqual(l.after,(0,))
        r,l2=refine_bounded(ctx,r,sw,(wq.gap,),wq.gap,(0,),5,lambda a:self.assertEqual(sh(a),(0,)))
        self.assertEqual(l2.after,(0,)); self.assertEqual(sh(r.assignments),(0,))
    def test_skip_still_locks(self):
        from core.model.model_builder import build_model
        from core.objectives.day_balance_constraints import add_day_balance
        from core.optimization.objective_days import refine_bounded
        from core.optimization.solver import solve
        data=fixture(2); ctx=build_model(data); _,w=day_sets(data,ctx.days)
        q=add_day_balance(ctx,data,w,'weekend'); ctx.model.minimize(q.gap); base=solve(ctx,5)
        score=lambda a:day_score(day_balance_rows(data,ctx.days,a),'weekend')
        _,layer=refine_bounded(ctx,base,score,(q.gap,),q.gap,(0,),5,lambda a:None)
        self.assertEqual(layer.status,'PROVEN_BOUND'); self.assertIsNone(layer.result)
        ctx.model.maximize(q.gap); r=solve(ctx,5); self.assertEqual(score(r.assignments),(0,))
    def test_timeout_fallback(self):
        from unittest.mock import patch
        from core.model.model_builder import build_model
        from core.objectives.day_balance_constraints import add_day_balance
        from core.optimization.objective_days import refine_bounded
        from core.optimization.solver import solve,SolveResult
        data=fixture(2); ctx=build_model(data); _,w=day_sets(data,ctx.days)
        q=add_day_balance(ctx,data,w,'weekend'); ctx.model.maximize(q.gap); base=solve(ctx,5)
        score=lambda a:day_score(day_balance_rows(data,ctx.days,a),'weekend')
        with patch('core.optimization.objective_days.solve_to_bound',return_value=SolveResult('UNKNOWN',None,1)):
            r,l=refine_bounded(ctx,base,score,(q.gap,),q.gap,(0,),5,lambda a:None)
        self.assertIs(r,base); self.assertEqual(l.before,l.after)
    def test_integration_sample_holidays(self):
        from pathlib import Path
        from core.data.data_loader import load_data
        from core.model.engine_model import build_stage3
        from core.optimization.objective_days import solve_quality43
        from core.validation.credit_checks import check_staffing
        from core.validation.admin_checks import check_admin_credits
        from core.validation.sequence_checks import check_sequences
        from core.validation.transition_checks import check_transitions
        root=Path(__file__).resolve().parents[1]
        data=load_data(root/'tests/fixtures/input/input.xlsx',root/'tests/fixtures/config/rules.json')
        if data.rules.get('shift_hours') is None:
            data.rules['shift_hours']={str(s):{'start':a,'end':b,'start_day_offset':o} for s,a,b,o in [(1,'22:00','06:00',-1),(2,'06:00','14:00',0),(3,'14:00','22:00',0)]}
        data=replace(data,holidays=data.holidays|frozenset((date(data.year,data.month,25),date(data.year,data.month,26))))
        ctx,demand,_,credit=build_stage3(data); q=solve_quality43(ctx,data,credit,demand,30,60,30,30,30)
        a=q.selected.assignments; self.assertIsNotNone(a)
        self.assertFalse(check_staffing(data,ctx.days,a)); self.assertFalse(check_admin_credits(data,ctx.days,a)[0]); self.assertFalse(check_sequences(data,ctx.days,a)[0]); self.assertFalse(check_transitions(data,ctx.days,a)[0])
        for l in (q.e01,q.e02,q.holiday,q.weekend):self.assertLessEqual(l.after,l.before)
        rows=day_balance_rows(data,ctx.days,a)
        self.assertEqual(day_score(rows,'holiday'),q.holiday.after); self.assertEqual(day_score(rows,'weekend'),q.weekend.after)

if __name__=='__main__': unittest.main()
