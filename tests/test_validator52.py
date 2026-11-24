import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import date,timedelta
from pathlib import Path
from core.data.data_loader import load_data
from core.engine.checkpoint import read_checkpoint
from core.objectives.preference_checks import load_preferences, Preference
from validator import validate_hard_rules, checkpoint_cells, Status

ROOT=Path(__file__).resolve().parents[1]; FIX=ROOT/'tests/fixtures'

class Validator52Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=load_data(FIX/'input/input.xlsx',FIX/'config/rules.json')
        cp=read_checkpoint(FIX/'output/checkpoints/2026-12_real.json')
        cls.codes={(i,d):c for i,d,c in checkpoint_cells(cls.data,cp.assignments)}
        cls.prefs=load_preferences(FIX/'input/input.xlsx',cls.data)
        cls.day=date(cls.data.year,cls.data.month,1)
    def report(self,changes=None,data=None,cells=None,prefs=None):
        codes=dict(self.codes); codes.update(changes or {})
        return validate_hard_rules(data or self.data,codes if cells is None else cells,prefs=self.prefs if prefs is None else prefs)
    def rule(self,report,k): return next(r for r in report.results if r.rule_id==k)
    def assert_fail(self,r,k): self.assertEqual(self.rule(r,k).status,Status.FAIL,k)
    def test_actual(self):
        r=self.report(); self.assertEqual(r.verdict,'INCOMPLETE'); self.assertFalse(r.errors)
        self.assertEqual(set(r.hard_pending),{'B02','B04','B05','B_WORK_TIME'})
        self.assertIn('SHIFT=284, ADMIN=115',self.rule(r,'C_CREDIT').note)
    def test_staff_missing(self):
        i,d=next(k for k,c in self.codes.items() if c=='2'); r=self.report({(i,d):'OFF'})
        self.assert_fail(r,'A02'); self.assert_fail(r,'C_DEMAND')
    def test_empty_shift(self):
        r=self.report({k:'OFF' for k,c in self.codes.items() if k[1]==self.day and c=='1'})
        self.assert_fail(r,'A01'); self.assert_fail(r,'A03')
    def test_excess(self):
        free=[k for k,c in self.codes.items() if k[1]==self.day and k[0]<=16 and c=='OFF'][:2]
        self.assert_fail(self.report(dict.fromkeys(free,'2')),'A04')
    def test_no_leader(self):
        leads={e.id for e in self.data.employees if e.is_leader}
        r=self.report({k:'OFF' for k,c in self.codes.items() if k[0] in leads and k[1]==self.day and c=='1'})
        self.assert_fail(r,'A03')
    def test_duplicate_ca_training(self):
        cells=[(i,d,c) for (i,d),c in self.codes.items()]; cells.append((5,date(2026,12,16),'1'))
        r=self.report(cells=cells); self.assert_fail(r,'A05'); self.assert_fail(r,'INPUT_GRID')
    def test_eligibility(self):
        r=self.report({(17,self.day):'1'}); self.assert_fail(r,'A06'); self.assert_fail(r,'D_ALLOWED')
    def test_continuity_history(self):
        p={k:('OFF' if k[1]==self.day-timedelta(days=1) else c) for k,c in self.data.previous.items()}
        r=self.report(data=replace(self.data,previous=p)); self.assert_fail(r,'A07')
    def test_night(self):
        r=self.report({(1,self.day+timedelta(days=j)):'1' for j in range(4)}); self.assert_fail(r,'B01')
    def test_work_chain(self):
        r=self.report({(1,self.day+timedelta(days=j)):'2' for j in range(6)}); self.assert_fail(r,'B03')
    def test_cross_month_night(self):
        p=dict(self.data.previous); p.update({(1,self.day-timedelta(days=j)):'1' for j in (1,2,3)})
        r=self.report({(1,self.day):'1'},data=replace(self.data,previous=p)); self.assert_fail(r,'B01')
        self.assertEqual(self.rule(r,'B01').findings[0].day,self.day)
    def test_dt_breaks_sequence(self):
        r=self.report({(1,self.day+timedelta(days=j)):c for j,c in enumerate(('1','1','DT','1','1','OFF'))})
        self.assertFalse(any(f.employee_id==1 and f.day<=self.day+timedelta(days=5) and f.day>=self.day+timedelta(days=2) for f in self.rule(r,'B01').findings))
    def test_training_conflict(self):
        r=self.report({(5,date(2026,12,16)):'1'}); self.assert_fail(r,'C_REQUEST_NONSHIFT')
        f=self.rule(r,'C_REQUEST_NONSHIFT').findings[0]; self.assertEqual((f.expected,f.actual),('DT','1'))
    def test_wrong_nonshift(self): self.assert_fail(self.report({(5,date(2026,12,16)):'CT'}),'C_REQUEST_NONSHIFT')
    def test_fixed_shift(self): self.assert_fail(self.report({(13,date(2026,12,14)):'OFF'}),'C08')
    def test_unrequested_training(self): self.assert_fail(self.report({(1,self.day):'DT'}),'C_CODES')
    def test_shift_t(self): self.assert_fail(self.report({(1,self.day):'T'}),'C_CODES')
    def test_admin_weekend(self): self.assert_fail(self.report({(17,date(2026,12,5)):'2'}),'D_ALLOWED')
    def test_admin_off_weekday(self): self.assert_fail(self.report({(17,self.day):'OFF'}),'D_T')
    def test_admin_cap(self):
        rules=deepcopy(self.data.rules); rules['admin_support']['max_shifts_per_person']=0
        self.assert_fail(self.report({(17,self.day):'2'},data=replace(self.data,rules=rules)),'D06')
    def test_admin_disabled(self):
        rules=deepcopy(self.data.rules); rules['admin_support']['enabled']=False
        self.assert_fail(self.report({(17,self.day):'2'},data=replace(self.data,rules=rules)),'D_ALLOWED')
    def test_daily_override(self):
        rules=deepcopy(self.data.rules); rules['credit_policy']['daily_demand']={self.day.isoformat():{'2':4}}
        r=self.report(data=replace(self.data,rules=rules)); self.assert_fail(r,'C_DEMAND')
        self.assertEqual(self.rule(r,'A02').status,Status.PASS)
    def test_missing_history(self):
        p=dict(self.data.previous); p.pop(next(iter(p))); r=self.report(data=replace(self.data,previous=p))
        self.assert_fail(r,'INPUT_HISTORY'); self.assertEqual(self.rule(r,'A07').status,Status.NOT_CHECKED)
        self.assertEqual(self.rule(r,'A02').status,Status.PASS)
    def test_invalid_config(self):
        rules=deepcopy(self.data.rules); rules['work_credit']['DT']=0
        r=self.report(data=replace(self.data,rules=rules)); self.assert_fail(r,'INPUT_CONFIG')
        self.assertEqual(self.rule(r,'C_CREDIT').status,Status.NOT_CHECKED)
    def test_invalid_preferences(self): self.assert_fail(self.report(prefs=(Preference(999,self.day,'PREFER_OFF'),)),'INPUT_CONFIG')
    def test_missing_cell(self):
        c=dict(self.codes); c.pop(next(iter(c))); r=self.report(cells=c)
        self.assert_fail(r,'INPUT_GRID'); self.assertEqual(self.rule(r,'A02').status,Status.NOT_CHECKED)
    def test_generator(self): self.assertFalse(self.report(cells=((i,d,c) for (i,d),c in self.codes.items())).errors)
    def test_no_solver_import(self):
        import subprocess,sys
        p=subprocess.run([sys.executable,'-c','import sys; import validator; assert not any(k.startswith("ortools") for k in sys.modules)'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
