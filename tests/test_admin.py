import unittest
from dataclasses import replace
from datetime import date
from importlib.util import find_spec
from pathlib import Path
from core.data.models import Employee, Request, MonthData
from core.data.calendar_utils import month_days
from core.validation.admin_checks import admin_policy, admin_allowed, validate_admin_requests, check_admin_credits

HAS_ORTOOLS=find_spec('ortools') is not None
if HAS_ORTOOLS:
    from core.model.model_builder import build_model
    from core.constraints.admin_constraints import add_admin_constraints, add_all_request_constraints, add_workforce_objective
    from core.optimization.solver import solve


def fixture():
    people=(Employee(1,'S1','SHIFT',True,(1,2,3),23),Employee(2,'S2','SHIFT',False,(1,2,3),23),
            Employee(3,'A1','ADMIN',False,(2,3)),Employee(4,'A2','ADMIN',False,(2,3)))
    rules={'max_shifts_per_day':1,'work_credit':{'1':1,'2':1,'3':1,'CT':1,'DT':1,'BKD':0,'OFF':0,'T':1},
           'admin_support':{'enabled':True,'allowed_shifts':[2,3],'allow_weekend':False,'allow_holiday':False,'max_shifts_per_person':None}}
    return MonthData(2026,12,frozenset({date(2026,12,25)}),people,(),{},rules)


def assignments(data):
    return {(e.id,d):0 for e in data.employees for d in month_days(data.year,data.month)}


class AdminChecksTests(unittest.TestCase):
    def test_allowed_days_shifts(self):
        data=fixture(); e=data.employees[2]; p=admin_policy(data)
        self.assertTrue(admin_allowed(e,date(2026,12,1),2,data,p))
        self.assertFalse(admin_allowed(e,date(2026,12,1),1,data,p))
        self.assertFalse(admin_allowed(e,date(2026,12,5),2,data,p))
        self.assertFalse(admin_allowed(e,date(2026,12,25),3,data,p))

    def test_holiday_toggle(self):
        data=fixture(); data.rules['admin_support']['allow_holiday']=True
        p=admin_policy(data)
        self.assertTrue(admin_allowed(data.employees[2],date(2026,12,25),3,data,p))
        self.assertFalse(admin_allowed(data.employees[2],date(2026,12,5),3,data,p))

    def test_disabled(self):
        data=fixture(); data.rules['admin_support']['enabled']=False
        self.assertFalse(admin_allowed(data.employees[2],date(2026,12,1),2,data,admin_policy(data)))

    def test_invalid_config(self):
        for key,value in [('allow_weekend',True),('allowed_shifts',[1,2]),('max_shifts_per_person',-1),('enabled','true')]:
            data=fixture(); data.rules['admin_support'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): admin_policy(data)

    def test_admin_t_replaced(self):
        data=fixture(); a=assignments(data); a[3,date(2026,12,1)]=2
        err,codes,rows=check_admin_credits(data,month_days(2026,12),a)
        self.assertFalse(err); self.assertEqual(codes[3,date(2026,12,1)],'2')
        self.assertEqual((rows[2].t,rows[2].shifts,rows[2].total),(21,1,22))
        self.assertEqual(rows[0].t,0)

    def test_special_codes_override_t(self):
        data=fixture(); data=replace(data,requests=tuple(Request(3,date(2026,12,k),c) for k,c in [(1,'CT'),(2,'DT'),(3,'BKD'),(4,'OFF')]))
        err,codes,rows=check_admin_credits(data,month_days(2026,12),assignments(data))
        self.assertFalse(err); self.assertEqual([codes[3,date(2026,12,k)] for k in range(1,5)],['CT','DT','BKD','OFF'])
        self.assertEqual((rows[2].t,rows[2].total),(18,20))

    def test_holiday_credit(self):
        data=fixture(); data.rules['admin_support']['allow_holiday']=True
        a=assignments(data); a[3,date(2026,12,25)]=3
        err,_,rows=check_admin_credits(data,month_days(2026,12),a)
        self.assertFalse(err); self.assertEqual((rows[2].total,rows[2].holiday_shifts,rows[2].excess),(23,1,1))

    def test_validator_illegal_admin(self):
        data=fixture(); a=assignments(data); a[3,date(2026,12,5)]=2
        self.assertTrue(check_admin_credits(data,month_days(2026,12),a)[0])

    def test_cap_and_fixed_request(self):
        data=fixture(); data.rules['admin_support']['max_shifts_per_person']=0
        a=assignments(data); a[3,date(2026,12,1)]=2
        self.assertTrue(check_admin_credits(data,month_days(2026,12),a)[0])
        data=replace(data,requests=(Request(3,date(2026,12,1),'2'),))
        with self.assertRaises(ValueError): validate_admin_requests(data)

    def test_fixed_admin_holiday_disabled(self):
        data=replace(fixture(),requests=(Request(3,date(2026,12,25),'2'),))
        with self.assertRaises(ValueError): validate_admin_requests(data)


@unittest.skipUnless(HAS_ORTOOLS,'Thiếu OR-Tools')
class AdminSolverTests(unittest.TestCase):
    def model(self,data,needed=1,active_days=1):
        # Mô hình nhỏ kiểm thử riêng cơ chế ADMIN/credit; không áp dụng nhóm A/B.
        ctx=build_model(data); add_admin_constraints(ctx,data); add_all_request_constraints(ctx,data)
        for k,d in enumerate(ctx.days):
            ctx.model.add(sum(ctx.worked[i,d] for i in ctx.employee_ids)==(needed if k<active_days else 0))
            for i in ctx.employee_ids:
                ctx.model.add(ctx.x[i,d,1]==0); ctx.model.add(ctx.x[i,d,3]==0)
        add_workforce_objective(ctx,data)
        return ctx

    def test_no_admin_when_no_excess(self):
        data=fixture(); ctx=self.model(data)
        r=solve(ctx); self.assertEqual(r.status,'OPTIMAL')
        self.assertEqual(sum(s!=0 for (i,d),s in r.assignments.items() if i in (3,4)),0)

    def test_admin_reduces_excess(self):
        data=fixture(); data=replace(data,employees=tuple(replace(e,target_credit=0) if e.group=='SHIFT' else e for e in data.employees))
        ctx=self.model(data,active_days=2); r=solve(ctx); self.assertEqual(r.status,'OPTIMAL')
        _,_,rows=check_admin_credits(data,ctx.days,r.assignments)
        self.assertEqual([x.shifts for x in rows],[0,0,1,1])

    def test_admin_disabled_even_if_excess(self):
        data=fixture(); data.rules['admin_support']['enabled']=False
        data=replace(data,employees=tuple(replace(e,target_credit=0) if e.group=='SHIFT' else e for e in data.employees))
        ctx=self.model(data); r=solve(ctx); self.assertEqual(r.status,'OPTIMAL')
        _,_,rows=check_admin_credits(data,ctx.days,r.assignments)
        self.assertEqual(sum(x.excess for x in rows[:2]),1); self.assertEqual(sum(x.shifts for x in rows[2:]),0)

    def test_support_if_staffing_impossible_without_admin(self):
        data=fixture(); ctx=self.model(data,needed=3); r=solve(ctx); self.assertEqual(r.status,'OPTIMAL')
        _,_,rows=check_admin_credits(data,ctx.days,r.assignments)
        self.assertEqual(sum(x.shifts for x in rows[2:]),1)

    def test_fixed_admin(self):
        data=replace(fixture(),requests=(Request(3,date(2026,12,1),'2'),))
        ctx=self.model(data); r=solve(ctx); self.assertEqual(r.status,'OPTIMAL')
        self.assertEqual(r.assignments[3,ctx.days[0]],2)

    def test_integration_sample(self):
        from core.data.data_loader import load_data
        from core.model.engine_model import build_stage3
        from core.validation.credit_checks import check_staffing
        from core.validation.sequence_checks import check_sequences
        from core.validation.transition_checks import check_transitions
        root=Path(__file__).resolve().parents[1]
        data=load_data(root/'tests/fixtures/input/input.xlsx',root/'tests/fixtures/config/rules.json')
        if data.rules.get('shift_hours') is None:
            data.rules['shift_hours']={str(s):{'start':a,'end':b,'start_day_offset':o} for s,a,b,o in [(1,'22:00','06:00',-1),(2,'06:00','14:00',0),(3,'14:00','22:00',0)]}
        ctx,demand,_,_=build_stage3(data); r=solve(ctx,time_limit=120)
        self.assertIsNotNone(r.assignments,r.status)
        errors,_,rows=check_admin_credits(data,ctx.days,r.assignments)
        self.assertFalse(errors); self.assertFalse(check_staffing(data,ctx.days,r.assignments))
        self.assertFalse(check_sequences(data,ctx.days,r.assignments)[0]); self.assertFalse(check_transitions(data,ctx.days,r.assignments)[0])
        self.assertEqual(sum(x.shifts for x in rows),sum(demand.values()))

if __name__=='__main__': unittest.main()
