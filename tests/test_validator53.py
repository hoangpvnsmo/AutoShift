import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import date,timedelta
from pathlib import Path
from core.data.data_loader import load_data
from core.engine.checkpoint import read_checkpoint
from core.objectives.preference_checks import load_preferences
from validator import validate_working_time, checkpoint_cells, Status

ROOT=Path(__file__).resolve().parents[1]; FIX=ROOT/'tests/fixtures'

class Validator53Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=load_data(FIX/'input/input.xlsx',FIX/'config/rules.json')
        cp=read_checkpoint(FIX/'output/checkpoints/2026-12_real.json')
        cls.codes={(i,d):c for i,d,c in checkpoint_cells(cls.data,cp.assignments)}
        cls.prefs=load_preferences(FIX/'input/input.xlsx',cls.data)
        cls.first=date(2026,12,1)
        cls.d=date(2026,12,15)
    def report(self,changes=None,data=None,policy=None,cells=None):
        c=dict(self.codes); c.update(changes or {})
        return validate_working_time(data or self.data,c if cells is None else cells,prefs=self.prefs,work_policy=policy)
    def rule(self,r,k): return next(x for x in r.results if x.rule_id==k)
    def fs(self,r,k,i=5): return [f for f in self.rule(r,k).findings if f.employee_id==i]
    def policy(self,**codes): return {'min_rest_hours':8,'codes':codes}
    def work(self,start='08:00',end='17:00'): return {'kind':'work','start':start,'end':end}
    def test_actual(self):
        r=self.report(); self.assertFalse(r.errors); self.assertEqual(r.hard_pending,('B_WORK_TIME',))
        for k in ('B02','B04','B05'): self.assertEqual(self.rule(r,k).status,Status.PASS)
        self.assertIn('8h',self.rule(r,'B04').note)
    def test_forbidden_and_zero_rest(self):
        r=self.report({(5,self.d):'3',(5,self.d+timedelta(days=1)):'1'})
        self.assertTrue(self.fs(r,'B02')); self.assertTrue(any(f.actual=='0h' for f in self.fs(r,'B04')))
    def test_exact_eight_hours(self):
        d=self.d; r=self.report({(5,d):'2',(5,d+timedelta(days=1)):'1'})
        self.assertFalse(any(f.day==d+timedelta(days=1) and f.related_day==d for f in self.fs(r,'B04')))
    def test_consecutive_nights(self):
        d=self.d; r=self.report({(5,d):'1',(5,d+timedelta(days=1)):'1'})
        self.assertFalse(any(f.day==d+timedelta(days=1) and f.related_day==d for f in self.fs(r,'B04')))
    def test_boundary(self):
        p=dict(self.data.previous); p[5,self.first-timedelta(days=1)]='3'
        r=self.report({(5,self.first):'1'},data=replace(self.data,previous=p))
        self.assertTrue(self.fs(r,'B05')); self.assertTrue(any(f.related_day==self.first-timedelta(days=1) for f in self.fs(r,'B04')))
    def test_past_only_not_flagged(self):
        p=dict(self.data.previous); p[5,self.first-timedelta(days=3)]='3'; p[5,self.first-timedelta(days=2)]='1'; p[5,self.first-timedelta(days=1)]='OFF'
        r=self.report({(5,self.first):'OFF'},data=replace(self.data,previous=p))
        self.assertFalse(any(f.day<self.first for f in self.fs(r,'B02')+self.fs(r,'B04')))
    def test_missing_history_not_pass(self):
        p=dict(self.data.previous); p.pop((5,self.first-timedelta(days=1)))
        r=self.report(data=replace(self.data,previous=p))
        self.assertEqual(self.rule(r,'B05').status,Status.NOT_CHECKED); self.assertEqual(self.rule(r,'B04').status,Status.NOT_CHECKED)
    def test_known_error_even_missing_history(self):
        p=dict(self.data.previous); p.pop((5,self.first-timedelta(days=1)))
        r=self.report({(5,self.d):'3',(5,self.d+timedelta(days=1)):'1'},data=replace(self.data,previous=p))
        self.assertEqual(self.rule(r,'B04').status,Status.FAIL)
    def test_bad_grid_pending(self):
        c=dict(self.codes); c.pop((5,self.d)); r=self.report(cells=c)
        self.assertEqual(self.rule(r,'B04').status,Status.NOT_CHECKED)
    def test_training_before_night(self):
        d=self.d; r=self.report({(5,d):'DT',(5,d+timedelta(days=1)):'1'},policy=self.policy(DT=self.work()))
        self.assertTrue(any(f.actual=='5h' and f.day==d+timedelta(days=1) for f in self.fs(r,'B_WORK_TIME')))
    def test_training_after_evening(self):
        d=self.d; r=self.report({(5,d):'3',(5,d+timedelta(days=1)):'DT'},policy=self.policy(DT=self.work('05:00','13:00')))
        self.assertTrue(any(f.actual=='7h' for f in self.fs(r,'B_WORK_TIME')))
    def test_phi_overlap(self):
        d=self.d; r=self.report({(5,d):'DT',(5,d+timedelta(days=1)):'1'},policy=self.policy(DT=self.work('18:00','23:00')))
        self.assertTrue(any(f.actual=='-1h' for f in self.fs(r,'B_WORK_TIME')))
    def test_phi_overlap_at_zero_min(self):
        d=self.d; p=self.policy(DT=self.work('18:00','23:00')); p['min_rest_hours']=0
        r=self.report({(5,d):'DT',(5,d+timedelta(days=1)):'1'},policy=p)
        self.assertTrue(any(f.actual=='-1h' for f in self.fs(r,'B_WORK_TIME')))
    def test_phi_unknown_partial(self):
        r=self.report(policy=self.policy(T=self.work('08:00','16:00')))
        self.assertEqual(self.rule(r,'B_WORK_TIME').status,Status.NOT_CHECKED)
    def test_full_phi_policy(self):
        # Test fixture only, NOT an approved operational schedule. Set 0h to test complete coverage.
        p={'min_rest_hours':0,'codes':{'DT':self.work('08:00','14:00'),'CT':self.work('08:00','14:00'),'T':self.work('08:00','14:00'),'BKD':{'kind':'rest'}}}
        r=self.report(policy=p); self.assertEqual(self.rule(r,'B_WORK_TIME').status,Status.PASS)
    def test_invalid_policy(self):
        r=self.report(policy=self.policy(DT={'kind':'rest'})); self.assertEqual(self.rule(r,'B_WORK_TIME').status,Status.FAIL)
    def test_bad_override(self):
        p=self.policy(DT=self.work()); p['overrides']=[{'id':5,'date':self.d.isoformat(),'code':'CT','activity':self.work()}]
        r=self.report(policy=p); self.assertTrue(any('Override' in f.message for f in self.fs(r,'B_WORK_TIME')))
    def test_override_actual_hours(self):
        d=self.d; p=self.policy(DT=self.work('08:00','13:00')); p['overrides']=[{'id':5,'date':d.isoformat(),'code':'DT','activity':self.work('08:00','19:00')}]
        r=self.report({(5,d):'DT',(5,d+timedelta(days=1)):'1'},policy=p)
        self.assertTrue(any(f.actual=='3h' for f in self.fs(r,'B_WORK_TIME')))
    def test_night_and_training_same_day(self):
        c=[(i,d,x) for (i,d),x in self.codes.items()]; c.append((5,self.d,'1'))
        r=self.report(cells=c); self.assertEqual(self.rule(r,'A05').status,Status.FAIL)
    def test_no_solver(self):
        import subprocess,sys
        p=subprocess.run([sys.executable,'-c','import sys; import validator; assert not any(k.startswith("ortools") for k in sys.modules)'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
    def test_generator(self):
        self.assertFalse(self.report(cells=((i,d,c) for (i,d),c in self.codes.items())).errors)
    def test_phi_cross_month(self):
        p=dict(self.data.previous); p[5,self.first-timedelta(days=1)]='DT'
        r=self.report({(5,self.first):'1'},data=replace(self.data,previous=p),policy=self.policy(DT=self.work()))
        self.assertTrue(any(f.related_day==self.first-timedelta(days=1) and f.actual=='5h' for f in self.fs(r,'B_WORK_TIME')))
    def test_rest_through_off_with_larger_threshold(self):
        rules=deepcopy(self.data.rules); rules['min_rest_hours']=24
        # ca3 end 22:00 day D -> ca1 start 22:00 D+1 for label D+2 = exactly 24h.
        d=self.d; r=self.report({(5,d):'3',(5,d+timedelta(days=1)):'OFF',(5,d+timedelta(days=2)):'1'},data=replace(self.data,rules=rules))
        self.assertFalse(any(f.related_day==d and f.day==d+timedelta(days=2) for f in self.fs(r,'B04')))
    def test_user_data_not_mutated(self):
        c=dict(self.codes); p=dict(self.data.previous); rules=deepcopy(self.data.rules)
        self.report(cells=c,policy=self.policy(DT=self.work()))
        self.assertEqual(c,self.codes); self.assertEqual(p,self.data.previous); self.assertEqual(rules,self.data.rules)

if __name__=='__main__': unittest.main()
