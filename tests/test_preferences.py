import unittest
from dataclasses import replace
from datetime import date
from tempfile import TemporaryDirectory
from pathlib import Path
from importlib.util import find_spec
from openpyxl import Workbook
from core.data.models import Employee,Request,MonthData
from core.data.calendar_utils import month_days
from core.objectives.preference_checks import (Preference,HEADERS,load_preferences,validate_preferences,satisfied,preference_lower,preference_rows,preference_score,demo_preferences)
from core.objectives.preference_constraints import add_preferences


def fixture():
    days=month_days(2026,12)
    rules={'max_shifts_per_day':1,'work_credit':{'1':1,'2':1,'3':1,'CT':1,'DT':1,'BKD':0,'T':1,'OFF':0},'admin_support':{'enabled':True,'allow_weekend':False,'allow_holiday':False,'allowed_shifts':[2,3],'max_shifts_per_person':None}}
    data=MonthData(2026,12,frozenset(),(Employee(1,'A','SHIFT',True,(1,2,3)),Employee(2,'B','SHIFT',False,(2,3)),Employee(3,'C','ADMIN',False,(2,3))), (Request(1,days[0],'CT'),Request(1,days[1],'2'),Request(1,days[2],'OFF')),{},rules)
    a={(e.id,d):0 for e in data.employees for d in days}; a[1,days[1]]=2
    return data,days,a

class PreferenceTests(unittest.TestCase):
    def test_semantics(self):
        for kind,s,yes in [('PREFER_OFF',None,{'OFF'}),('PREFER_WORK',None,{'1','2','3'}),('PREFER_SHIFT',2,{'2'}),('AVOID_SHIFT',2,{'1','3','OFF','CT','DT','BKD','T'})]:
            p=Preference(1,date(2026,12,1),kind,s)
            self.assertEqual({c for c in ('1','2','3','OFF','CT','DT','BKD','T') if satisfied(p,c)},yes)
    def test_hard_conflicts(self):
        d,days,a=fixture(); prefs=[Preference(1,days[0],'PREFER_OFF',weight=2),Preference(1,days[1],'AVOID_SHIFT',2),Preference(1,days[2],'PREFER_WORK'),Preference(2,days[0],'PREFER_SHIFT',1)]
        self.assertEqual(preference_lower(d,prefs),(5,)); self.assertEqual(preference_score(preference_rows(d,days,a,prefs)),(5,))
    def test_admin_off_not_t(self):
        d,days,a=fixture(); p=[Preference(3,days[0],'PREFER_OFF'),Preference(3,days[4],'PREFER_WORK')]
        self.assertEqual(preference_lower(d,p),(2,)); self.assertEqual([r[1] for r in preference_rows(d,days,a,p)],['T','OFF'])
    def test_weighted(self):
        d,days,a=fixture(); p=[Preference(1,days[1],'PREFER_SHIFT',2,4),Preference(1,days[3],'PREFER_WORK',weight=3)]
        self.assertEqual(preference_score(preference_rows(d,days,a,p)),(3,)); self.assertEqual(preference_lower(d,p),(0,))
    def test_duplicate(self):
        d,days,a=fixture(); p=Preference(1,days[0],'PREFER_OFF')
        with self.assertRaises(ValueError): validate_preferences(d,[p,p])
    def test_invalid(self):
        d,days,a=fixture()
        for p in [Preference(9,days[0],'PREFER_OFF'),Preference(1,date(2027,1,1),'PREFER_OFF'),Preference(1,days[0],'bad'),Preference(1,days[0],'PREFER_SHIFT'),Preference(1,days[0],'PREFER_OFF',1),Preference(1,days[0],'PREFER_OFF',weight=0)]:
            with self.subTest(p=p),self.assertRaises(ValueError): validate_preferences(d,[p])
    def test_optional_sheet(self):
        d,days,a=fixture()
        with TemporaryDirectory() as temp:
            f=Path(temp)/'test.xlsx'; w=Workbook(); w.save(f); w.close(); self.assertEqual(load_preferences(f,d),())
    def test_read_sheet(self):
        d,days,a=fixture()
        with TemporaryDirectory() as temp:
            f=Path(temp)/'test.xlsx'; w=Workbook(); s=w.create_sheet('Preferences'); s.append(HEADERS); s.append([1,days[3],'prefer_off',None,None]); s.append([2,days[4],'PREFER_SHIFT',2,3]); w.save(f); w.close()
            self.assertEqual(load_preferences(f,d),(Preference(1,days[3],'PREFER_OFF'),Preference(2,days[4],'PREFER_SHIFT',2,3)))
    def test_demo(self):
        d,days,a=fixture(); p=demo_preferences(d); self.assertEqual(len(p),4); self.assertEqual({x.kind for x in p},{'PREFER_OFF','PREFER_SHIFT','AVOID_SHIFT','PREFER_WORK'})

@unittest.skipUnless(find_spec('ortools') is not None,'Thiếu OR-Tools')
class SolverTests(unittest.TestCase):
    def test_model_score(self):
        from core.model.model_builder import build_model
        from ortools.sat.python import cp_model
        d,days,a=fixture(); ctx=build_model(d)
        p=(Preference(1,days[0],'PREFER_OFF',weight=2),Preference(1,days[1],'AVOID_SHIFT',2),Preference(1,days[2],'PREFER_OFF'),Preference(2,days[3],'PREFER_WORK'),Preference(3,days[0],'PREFER_OFF'),Preference(3,days[4],'PREFER_OFF'))
        terms,obj,bounds=add_preferences(ctx,d,p)
        for (i,day,s),v in ctx.x.items(): ctx.model.add(v==int(a[i,day]==s))
        solver=cp_model.CpSolver(); self.assertEqual(solver.solve(ctx.model),cp_model.OPTIMAL)
        self.assertEqual((solver.value(terms[0]),),preference_score(preference_rows(d,days,a,p)))
    def test_lock_previous_score(self):
        from core.model.model_builder import build_model
        from ortools.sat.python import cp_model
        d,days,a=fixture(); ctx=build_model(d); m=ctx.model
        # Điểm ưu tiên cũ khóa ca 2; E06 muốn ca 3 nhưng không được ghi đè.
        m.add(ctx.x[1,days[3],2]==1)
        terms,obj,bounds=add_preferences(ctx,d,(Preference(1,days[3],'PREFER_SHIFT',3),Preference(2,days[4],'PREFER_SHIFT',2)))
        m.minimize(obj); solver=cp_model.CpSolver(); self.assertEqual(solver.solve(m),cp_model.OPTIMAL)
        self.assertEqual(solver.value(terms[0]),1); self.assertEqual(solver.value(ctx.x[2,days[4],2]),1)
