import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from importlib.util import find_spec
from core.data.data_loader import load_data
from core.data.calendar_utils import month_days
from core.engine.checkpoint import read_checkpoint,fingerprints,reusable_prefix
from core.engine.engine_checkpoint import historical_fingerprints,migrate_checkpoint
from core.objectives.preference_checks import load_preferences
from core.objectives.flexible_rotation import flexible_rows,flexible_score,flexible_targets,known_windows
from core.reporting.excel_report import write_full_report
from validator import checkpoint_cells,validate_quality,score_vector,read_schedule
from openpyxl import load_workbook
from test_rest_balance import fixture
ROOT=Path(__file__).resolve().parents[1];FIX=ROOT/'tests/fixtures'
class Flexible49Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=load_data(FIX/'input/input.xlsx',FIX/'config/rules.json');cls.days=month_days(cls.data.year,cls.data.month)
        cls.cp=read_checkpoint(FIX/'output/checkpoints/2026-12_real.json');cls.prefs=load_preferences(FIX/'input/input.xlsx',cls.data)
        cls.codes={(i,d):c for i,d,c in checkpoint_cells(cls.data,cls.cp.assignments)}
    def blank(self):
        d,days,a=fixture(n=2);a={k:0 for k in a};d=replace(d,previous={k:'OFF' for k in d.previous});return d,days,a
    def point(self,seq):
        d,days,a=self.blank()
        for day,s in zip(days,seq):a[1,day]=s
        return flexible_score(flexible_rows(d,days,a))
    def test_forward_good(self):
        for seq in ((1,1,2),(1,2,3),(2,2,3,3)):self.assertEqual(self.point(seq),(0,0,0,0))
    def test_reverse_short_first(self): self.assertEqual(self.point((2,1))[0],1)
    def test_streak(self):
        self.assertEqual(self.point((1,1,1))[1],1);self.assertEqual(self.point((2,2,2,2,2))[1],3)
    def test_across_off(self):
        s=self.point((1,1,0,0,1,1,0));self.assertEqual(s[1],0);self.assertGreater(s[2],0)
    def test_history(self):
        d,days,a=self.blank();d.previous[1,days[0]-timedelta(days=1)]='1';d.previous[1,days[0]-timedelta(days=2)]='1';a[1,days[0]]=1
        self.assertEqual(flexible_score(flexible_rows(d,days,a))[1],1)
    def test_single_type_exempt(self):
        d,days,a=self.blank();d=replace(d,employees=(replace(d.employees[0],allowed_shifts=(2,)),d.employees[1]));
        for day in days:a[1,day]=2
        self.assertEqual(flexible_score(flexible_rows(d,days,a)),(0,0,0,0))
    def test_actual_scores_independent(self):
        r=validate_quality(self.data,self.codes,prefs=self.prefs);v=score_vector(r)
        self.assertEqual(v[:7],self.cp.scores[:7]);self.assertEqual(v[7],(1,85,160,29));self.assertEqual(v[7],flexible_score(flexible_rows(self.data,self.days,self.cp.assignments)));self.assertFalse(r.errors)
    def test_targets(self):
        d,days,a=self.blank()
        for day,s in zip(days,(1,1,1,0,1,1,0)):a[1,day]=s
        self.assertTrue(any(i==1 and x==days[2] for i,x,w in flexible_targets(d,days,a)))
        self.assertTrue(all(x in days and w>0 for i,x,w in flexible_targets(d,days,a)))
    def test_migrate_56_revoke_e05_e06(self):
        h=historical_fingerprints(self.data,self.prefs,ROOT,'56');new=fingerprints(self.data,self.prefs,ROOT)
        old=replace(self.cp,base_hash=h[0],pref_hash=h[1],proven_prefix=9);cp,v=migrate_checkpoint(old,self.data,self.prefs,ROOT,new)
        scores=self.cp.scores[:7]+((1,85,160,29),)+self.cp.scores[8:]
        self.assertEqual(v,'56');self.assertEqual(cp.proven_prefix,7);self.assertEqual(reusable_prefix(cp,*new,scores),7)
        self.assertEqual(reusable_prefix(cp,*new,((99,0,0,0,0),)+scores[1:]),0)
    def test_window_missing_boundary(self):
        d,days,a=self.blank();self.assertFalse(any(w[-1]==days[0] for w in known_windows(d,days,1)));self.assertTrue(any(w[-1]==days[1] for w in known_windows(d,days,1)))
    def test_excel_roundtrip_and_blank(self):
        report=validate_quality(self.data,self.codes,prefs=self.prefs)
        with TemporaryDirectory() as t:
            p=Path(t)/'out.xlsx';write_full_report(p,self.data,self.codes,report,old_vector=score_vector(report))
            self.assertEqual({(i,d):c for i,d,c in read_schedule(p,self.data)},self.codes)
            wb=load_workbook(p);self.assertEqual(wb.sheetnames,['Lich truc','Thong ke','Bo tri ca','Kiem tra','Schedule','Huong dan']);self.assertEqual(wb['Schedule']['F2'].value,'OFF');self.assertEqual(wb['Lich truc']['F6'].value,'=IF(Schedule!F2="OFF","",IF(Schedule!F2="","?",Schedule!F2))');self.assertIn('COUNTIF',wb['Thong ke']['K5'].value);wb.close()
            b=p.read_bytes();self.assertRaises(FileExistsError,write_full_report,p,self.data,self.codes,report);self.assertEqual(p.read_bytes(),b)
    @unittest.skipUnless(find_spec('ortools') is not None,'Thiếu OR-Tools')
    def test_cp_sat_exact_score(self):
        from core.model.model_builder import build_model
        from core.objectives.flexible_rotation import add_flexible_rotation
        from ortools.sat.python import cp_model
        d,days,a=self.blank()
        for day,s in zip(days,(1,1,1,0,1,1,2,3,2)):a[1,day]=s
        d.previous[1,days[0]-timedelta(days=1)]='1'
        ctx=build_model(d);t=add_flexible_rotation(ctx,d)
        for (i,day,s),v in ctx.x.items():ctx.model.add(v==int(a[i,day]==s))
        solver=cp_model.CpSolver();self.assertEqual(solver.solve(ctx.model),cp_model.OPTIMAL);self.assertEqual(tuple(solver.value(v) for v in t.terms),flexible_score(flexible_rows(d,days,a)))
if __name__=='__main__':unittest.main()
