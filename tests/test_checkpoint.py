from datetime import date
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from core.engine.checkpoint import (Checkpoint, checkpoint_path, read_checkpoint,write_checkpoint, fingerprints,
                             reusable_prefix,prefix_length,active_vector,LENGTHS)
from core.data.models import MonthData,Employee,Request
from core.objectives.preference_checks import Preference

D=date(2026,12,1)
S=tuple(tuple(0 for _ in range(n)) for n in LENGTHS)

class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
        self.data=MonthData(2026,12,frozenset(),(Employee(1,'A','SHIFT',True,(1,2,3)),),(),{(1,date(2026,11,30)):'OFF'},{'optimization':{}})
        self.hashes=fingerprints(self.data,(),self.root)
        self.cp=Checkpoint(*self.hashes,{(1,D):2},S,8)
        self.path=checkpoint_path(self.root,2026,12)
    def test_roundtrip(self):
        self.assertIsNone(read_checkpoint(self.path)); write_checkpoint(self.path,self.cp)
        self.assertEqual(read_checkpoint(self.path),self.cp)
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])
    def test_replace(self):
        write_checkpoint(self.path,self.cp); cp=replace(self.cp,assignments={(1,D):3}); write_checkpoint(self.path,cp)
        self.assertEqual(read_checkpoint(self.path),cp)
    def test_corrupt(self):
        write_checkpoint(self.path,self.cp); raw=json.loads(self.path.read_text()); raw['payload']['assignments'][0][-1]=1
        self.path.write_text(json.dumps(raw)); self.assertRaises(ValueError,read_checkpoint,self.path)
    def test_scenario_isolation(self):
        paths=[self.path,checkpoint_path(self.root,2026,12,17),checkpoint_path(self.root,2026,12,holidays=[D]),checkpoint_path(self.root,2026,12,demo_preferences=True),checkpoint_path(self.root,2027,1)]
        self.assertEqual(len(set(paths)),5)
        self.assertEqual(checkpoint_path(self.root,2026,12,holidays=[D,D]),paths[2])
    def test_pref_only(self):
        h=fingerprints(self.data,[Preference(1,D,'PREFER_OFF')],self.root)
        self.assertEqual(h[0],self.hashes[0]); self.assertNotEqual(h[1],self.hashes[1]); self.assertEqual(reusable_prefix(replace(self.cp,proven_prefix=9),*h,S),8)
    def test_changed_hard(self):
        h=fingerprints(replace(self.data,requests=(Request(1,D,'OFF'),)),(),self.root)
        self.assertEqual(reusable_prefix(self.cp,*h,S),0)
    def test_changed_source(self):
        (self.root/'core').mkdir(); (self.root/'core/foo.py').write_text('x=1')
        self.assertNotEqual(fingerprints(self.data,(),self.root)[0],self.hashes[0])
    def test_same_input(self): self.assertEqual(reusable_prefix(self.cp,*self.hashes,S),8)
    def test_score_mismatch(self): self.assertEqual(reusable_prefix(self.cp,*self.hashes,((1,0,0,0,0),)+S[1:]),0)
    def test_prefix(self): self.assertEqual(prefix_length(['OPTIMAL','PROVEN_BOUND','NO_DAYS','FEASIBLE','OPTIMAL']),3)
    def test_priority(self):
        a=((0,),(8,)); b=((1,),(0,)); self.assertLess(active_vector(a,(True,True)),active_vector(b,(True,True)))
        self.assertGreater(active_vector(a,(False,True)),active_vector(b,(False,True)))

try:
    import ortools
except ImportError:
    ortools=None

@unittest.skipIf(ortools is None,'OR-Tools chưa cài')
class ModelTests(unittest.TestCase):
    def test_fixed_model_valid_and_invalid(self):
        from core.model.engine_model import build_stage3
        from core.data.data_loader import load_data
        from core.legacy.objective_resume import validate_incumbent
        from core.optimization.solver import solve
        root=Path(__file__).resolve().parents[1]; data=load_data(root/'tests/fixtures/input/input.xlsx',root/'tests/fixtures/config/rules.json')
        ctx,*_=build_stage3(data); result=solve(ctx,30)
        self.assertIsNotNone(result.assignments)
        self.assertTrue(validate_incumbent(ctx,data,result.assignments)[0])
        wrong={k:0 for k in result.assignments}; self.assertFalse(validate_incumbent(ctx,data,wrong)[0])
    def test_tie_preserves_best(self):
        from core.legacy.objective_resume import choose_on_tie
        from core.optimization.solver import SolveResult
        a=SolveResult('FEASIBLE',{(1,D):1},0); b=SolveResult('FEASIBLE',{(1,D):2},0)
        score=lambda x:(0,); downstream=lambda x:(x[1,D],)
        self.assertIs(choose_on_tie(a,b,score,(score,downstream),(True,True)),a)
        self.assertIs(choose_on_tie(b,a,score,(score,downstream),(True,True)),a)
    def test_resume_proven_prefix_and_changed_preferences(self):
        from core.model.engine_model import build_stage3
        from core.data.data_loader import load_data
        from core.legacy.objective_resume import solve_quality47,make_checkpoint
        root=Path(__file__).resolve().parents[1]; data=load_data(root/'tests/fixtures/input/input.xlsx',root/'tests/fixtures/config/rules.json')
        disabled={k:False for k in ('balance_shift_types','balance_night_shifts','balance_holiday_shifts','balance_weekend_shifts','balance_rest_days','minimize_shift_changes')}
        data=replace(data,rules={**data.rules,'optimization':disabled})
        ctx,dem,_,credit=build_stage3(data); h=fingerprints(data,(),root)
        q,*_=solve_quality47(ctx,data,credit,dem,base_time=30,preferences=())
        self.assertIsNotNone(q.selected.assignments); self.assertTrue(q.proven)
        cp=make_checkpoint(ctx,data,(),q,h); self.assertEqual(cp.proven_prefix,9)
        ctx,dem,_,credit=build_stage3(data)
        q,valid,_,prefix=solve_quality47(ctx,data,credit,dem,base_time=30,saved=cp,hashes=h)
        self.assertTrue(valid); self.assertEqual(prefix,9); self.assertEqual(q.baseline.wall_time,0); self.assertEqual(q.selected.assignments,cp.assignments)
        # Choose AVOID_SHIFT for a shift not assigned by the old solution.
        i,day=next(iter(cp.assignments)); s=next(s for s in (1,2,3) if s!=cp.assignments[i,day]); prefs=(Preference(i,day,'AVOID_SHIFT',s),)
        h2=fingerprints(data,prefs,root); ctx,dem,_,credit=build_stage3(data)
        q,valid,_,prefix=solve_quality47(ctx,data,credit,dem,base_time=30,preferences=prefs,saved=cp,hashes=h2)
        self.assertTrue(valid); self.assertEqual(prefix,8); self.assertEqual(q.baseline.wall_time,0); self.assertEqual(q.preferences.after,(0,)); self.assertTrue(q.proven)
