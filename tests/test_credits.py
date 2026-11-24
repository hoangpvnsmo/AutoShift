import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from importlib.util import find_spec
from core.data.models import Employee,Request,MonthData
from core.data.calendar_utils import month_days
from core.validation.credit_checks import demand_requirements,request_map,check_credits,check_work_credit,check_staffing

HAS_ORTOOLS = find_spec('ortools') is not None
if HAS_ORTOOLS:
    from core.model.model_builder import build_model
    from core.constraints.credit_constraints import add_demand_constraints,add_request_constraints,add_credit_objective
    from core.optimization.solver import solve
    from core.constraints.hard_constraints import (add_staffing_constraints,add_shift_eligibility,disable_admin_for_trial,
        add_previous_day_continuity,add_consecutive_shift_constraints,add_shift_transition_constraints)
    from core.validation.sequence_checks import check_sequences
    from core.validation.transition_checks import check_transitions


def fixture():
    d = date(2026,12,1)
    e = Employee(1,'E1','SHIFT',True,(1,2,3))
    rules = {'max_shifts_per_day':1,'staff_per_shift':{str(s):{'min':3,'preferred':4,'max':4} for s in (1,2,3)},
             'work_credit':{'1':1,'2':1,'3':1,'CT':1,'DT':1,'BKD':0,'OFF':0,'T':1}}
    req = tuple(Request(1,date(2026,12,k),c) for k,c in [(1,'CT'),(2,'DT'),(3,'BKD'),(4,'OFF'),(5,'2')])
    return MonthData(2026,12,frozenset(),(e,),req,{},rules)


class CreditChecksTests(unittest.TestCase):
    def test_default_demand_not_preferred(self):
        self.assertEqual(sum(demand_requirements(fixture()).values()),279)

    def test_custom_demand(self):
        data = fixture()
        data.rules['credit_policy']={'demand_staff_per_shift':{'2':4},'daily_demand':{'2026-12-01':{'3':4}}}
        self.assertEqual(sum(demand_requirements(data).values()),311)

    def test_invalid_demand(self):
        data = fixture()
        data.rules['credit_policy']={'demand_staff_per_shift':{'1':5}}
        with self.assertRaises(ValueError): demand_requirements(data)

    def test_daily_outside_month(self):
        data = fixture()
        data.rules['credit_policy']={'daily_demand':{'2026-11-30':{'1':3}}}
        with self.assertRaises(ValueError): demand_requirements(data)

    def test_credit_and_requests(self):
        data=fixture(); days=month_days(data.year,data.month)
        a={(1,d):2 if d.day==5 else 0 for d in days}
        errors,codes,rows=check_credits(data,days,a)
        self.assertFalse(errors)
        r=rows[0]
        self.assertEqual((r.total,r.missing,r.excess,r.bkd,r.off),(3,20,0,1,27))
        self.assertEqual([codes[1,d] for d in days[:5]],['CT','DT','BKD','OFF','2'])

    def test_fixed_and_nonshift_violations(self):
        data=fixture(); days=month_days(data.year,data.month)
        a={(1,d):1 if d.day<=5 else 0 for d in days}
        self.assertEqual(len(check_credits(data,days,a)[0]),5)

    def test_duplicate_request(self):
        data=fixture(); data=replace(data,requests=data.requests+(data.requests[0],))
        with self.assertRaises(ValueError): request_map(data)

    def test_T_not_allowed(self):
        data=fixture(); data=replace(data,requests=(Request(1,date(2026,12,1),'T'),))
        with self.assertRaises(ValueError): request_map(data)

    def test_excess(self):
        data=fixture(); data=replace(data,employees=(replace(data.employees[0],target_credit=2),))
        days=month_days(data.year,data.month)
        a={(1,d):2 if d.day==5 else 0 for d in days}
        r=check_credits(data,days,a)[2][0]
        self.assertEqual((r.excess,r.missing),(1,0))

    def test_invalid_credit_mapping(self):
        data=fixture(); data.rules['work_credit']['BKD']=1
        with self.assertRaises(ValueError): check_work_credit(data)


@unittest.skipUnless(HAS_ORTOOLS,'Thiếu OR-Tools')
class CreditSolverTests(unittest.TestCase):
    def test_fixed_requests(self):
        data=fixture(); ctx=build_model(data); add_request_constraints(ctx,data)
        r=solve(ctx)
        self.assertIsNotNone(r.assignments)
        self.assertEqual([r.assignments[1,d] for d in ctx.days[:5]],[0,0,0,0,2])

    def test_forbidden_request(self):
        data=fixture(); ctx=build_model(data); add_request_constraints(ctx,data)
        ctx.model.add(ctx.x[1,ctx.days[0],1]==1)
        self.assertEqual(solve(ctx).status,'INFEASIBLE')

    def test_fixed_shift_must_hold(self):
        data=fixture(); ctx=build_model(data); add_request_constraints(ctx,data)
        ctx.model.add(ctx.x[1,ctx.days[4],2]==0)
        self.assertEqual(solve(ctx).status,'INFEASIBLE')

    def test_admin_fixed_shift_blocked(self):
        data=fixture(); e=Employee(1,'A','ADMIN',False,(2,3))
        data=replace(data,employees=(e,),requests=(Request(1,date(2026,12,1),'2'),))
        with self.assertRaisesRegex(ValueError,'hành chính'): add_request_constraints(build_model(data),data)

    def test_demand_and_objective(self):
        data=fixture()
        # Mô hình nhỏ, chỉ kiểm tra tổng công và công bằng (không thêm A/B).
        people=tuple(Employee(i,f'E{i}','SHIFT',False,(1,2,3)) for i in range(1,17))
        data=replace(data,employees=people,requests=())
        ctx=build_model(data); add_demand_constraints(ctx,data); add_request_constraints(ctx,data)
        add_credit_objective(ctx,data)
        # Ghim một nghiệm công bằng có thể dựng bằng vòng quay 9 người/ngày.
        for k,d in enumerate(ctx.days):
            for j in range(9):
                i=(9*k+j)%16+1
                ctx.model.add(ctx.x[i,d,j//3+1]==1)
        r=solve(ctx,time_limit=30)
        self.assertIsNotNone(r.assignments)
        errors,_,rows=check_credits(data,ctx.days,r.assignments)
        self.assertFalse(errors)
        self.assertEqual(sum(x.total for x in rows),279)
        self.assertEqual(max(x.total for x in rows)-min(x.total for x in rows),1)
        self.assertEqual(sum(x.excess for x in rows),0)

    def test_integration_sample(self):
        from core.data.data_loader import load_data
        root=Path(__file__).resolve().parents[1]
        data=load_data(root/'tests/fixtures/input/input.xlsx',root/'tests/fixtures/config/rules.json')
        if data.rules.get('shift_hours') is None:
            data.rules['shift_hours']={str(s):{'start':a,'end':b,'start_day_offset':o} for s,a,b,o in [(1,'22:00','06:00',-1),(2,'06:00','14:00',0),(3,'14:00','22:00',0)]}
        ctx=build_model(data)
        for fn in (add_staffing_constraints,add_shift_eligibility,disable_admin_for_trial,
                   add_previous_day_continuity,add_consecutive_shift_constraints,add_shift_transition_constraints,
                   add_demand_constraints,add_request_constraints,add_credit_objective): fn(ctx,data)
        r=solve(ctx,time_limit=120)
        self.assertIsNotNone(r.assignments, r.status)
        self.assertFalse(check_credits(data,ctx.days,r.assignments)[0])
        self.assertFalse(check_staffing(data,ctx.days,r.assignments))
        self.assertFalse(check_sequences(data,ctx.days,r.assignments)[0])
        self.assertFalse(check_transitions(data,ctx.days,r.assignments)[0])

if __name__=='__main__': unittest.main()
