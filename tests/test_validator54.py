import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import date,timedelta
from pathlib import Path
from core.data.data_loader import load_data
from core.engine.checkpoint import read_checkpoint, source_hashes
from core.objectives.preference_checks import load_preferences, Preference
from validator import validate_quality, score_vector, checkpoint_cells, Status
from validator.working_time import parse_work_policy
from validator.quality import OBJECTIVE_IDS
ROOT=Path(__file__).resolve().parents[1]; FIX=ROOT/'tests/fixtures'

class Validator54Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=load_data(FIX/'input/input.xlsx',FIX/'config/rules.json')
        cls.cp=read_checkpoint(FIX/'output/checkpoints/2026-12_real.json')
        from dataclasses import replace
        cls.cp=replace(cls.cp,scores=cls.cp.scores[:7]+((1,85,160,29),)+cls.cp.scores[8:])
        cls.codes={(i,d):c for i,d,c in checkpoint_cells(cls.data,cls.cp.assignments)}
        cls.prefs=load_preferences(FIX/'input/input.xlsx',cls.data)
    def run_report(self,changes=None,data=None,prefs=None,expected=None,policy=None,cells=None):
        c=dict(self.codes); c.update(changes or {})
        return validate_quality(data or self.data,c if cells is None else cells,prefs=self.prefs if prefs is None else prefs,expected_scores=expected,work_policy=policy)
    def rule(self,r,k): return next(x for x in r.results if x.rule_id==k)
    def test_actual_vector(self):
        r=self.run_report(expected=self.cp.scores)
        self.assertEqual(score_vector(r),self.cp.scores)
        self.assertEqual(r.hard_pending,('B_WORK_TIME',)); self.assertFalse(r.errors)
        self.assertTrue(all('Khớp điểm tham chiếu' in self.rule(r,k).note for k in OBJECTIVE_IDS))
    def test_target(self):
        self.assertEqual(self.rule(self.run_report(),'C_TARGET').score,(84,0,0,0))
    def test_short_warning(self):
        r=self.run_report(); f=self.rule(r,'E05').findings
        self.assertEqual([(x.employee_id,x.day,x.actual) for x in f],[(11,date(2026,12,20),'8h')])
    def test_preference_warning(self):
        f=self.rule(self.run_report(),'E06').findings
        self.assertEqual([(x.employee_id,x.day,x.actual) for x in f],[(2,date(2026,12,10),'OFF')])
    def test_preference_weight(self):
        p=tuple(replace(x,weight=5) if x.employee_id==2 else x for x in self.prefs)
        self.assertEqual(self.rule(self.run_report(prefs=p),'E06').score,(5,))
    def test_no_preferences(self): self.assertEqual(self.rule(self.run_report(prefs=()),'E06').score,(0,))
    def test_actual_off_not_reconstructed(self):
        d=date(2026,12,15); r=self.run_report({(5,d):'OFF'})
        self.assertTrue(r.errors); self.assertEqual(self.rule(r,'C_TARGET').score,(85,0,0,0))
        self.assertNotEqual(self.rule(r,'E04a').score,self.cp.scores[5])
    def test_holiday_weekend_disjoint(self):
        d=date(2026,12,5); r=self.run_report(data=replace(self.data,holidays=frozenset({d})))
        self.assertIn('1 ngày; 9 lượt SHIFT',self.rule(r,'E03a').note)
        self.assertIn('7 ngày; 63 lượt SHIFT',self.rule(r,'E03b').note)
    def test_missing_history(self):
        p=dict(self.data.previous); p.pop(next(iter(p))); r=self.run_report(data=replace(self.data,previous=p))
        self.assertIsNone(score_vector(r)); self.assertEqual(self.rule(r,'E05').status,Status.NOT_CHECKED)
        self.assertEqual(self.rule(r,'E01').status,Status.MEASURED)
    def test_missing_grid(self):
        c=dict(self.codes); c.pop(next(iter(c))); r=self.run_report(cells=c)
        self.assertIsNone(score_vector(r)); self.assertEqual(self.rule(r,'STAGE3').status,Status.NOT_CHECKED)
    def test_reference_mismatch(self):
        s=list(self.cp.scores); s[7]=(10,1,2,2); r=self.run_report(expected=s)
        self.assertEqual(score_vector(r),self.cp.scores); self.assertFalse(r.errors)
        self.assertTrue(any('tham chiếu' in f.message for f in self.rule(r,'E05').findings))
    def test_reference_invalid(self):
        with self.assertRaises(ValueError): self.run_report(expected=[(0,)])
    def test_disabled(self):
        rules=deepcopy(self.data.rules); rules['optimization']['minimize_shift_changes']=False
        r=self.run_report(data=replace(self.data,rules=rules)); self.assertEqual(self.rule(r,'E05').score,self.cp.scores[7]); self.assertIn('Tầng tắt',self.rule(r,'E05').note)
    def test_bkd_rest(self):
        _,defaults,_=parse_work_policy(self.data,{'min_rest_hours':0,'codes':{}}); self.assertEqual(defaults['BKD'],())
        with self.assertRaises(ValueError): parse_work_policy(self.data,{'min_rest_hours':0,'codes':{'BKD':{'kind':'work','start':'08:00','end':'17:00'}}})
    def test_phi_optional(self):
        d=date(2026,12,16); r=self.run_report({(5,d+timedelta(days=1)):'1'})
        self.assertEqual(self.rule(r,'B_WORK_TIME').status,Status.NOT_CHECKED); self.assertFalse(self.rule(r,'B_WORK_TIME').findings)
        self.assertIn('BKD không làm việc',self.rule(r,'B_WORK_TIME').note)
    def test_generator(self): self.assertEqual(score_vector(self.run_report(cells=((i,d,c) for (i,d),c in self.codes.items()))),self.cp.scores)
    def test_no_solver_or_mutation(self):
        import sys
        before=source_hashes(ROOT); c=dict(self.codes); rules=deepcopy(self.data.rules); self.run_report(cells=c)
        self.assertEqual(before,source_hashes(ROOT)); self.assertEqual(c,self.codes); self.assertEqual(rules,self.data.rules)
        # Other test modules may have imported solver; check isolated process.
        import subprocess
        p=subprocess.run([sys.executable,'-c','import validator,sys; assert not any(x.startswith("ortools") for x in sys.modules)'],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
    def test_json(self):
        import json
        d=json.loads(json.dumps(self.run_report().to_dict(),default=str)); self.assertEqual(next(x['score'] for x in d['rules'] if x['rule_id']=='E05'),[1,85,160,29])
if __name__=='__main__': unittest.main()
