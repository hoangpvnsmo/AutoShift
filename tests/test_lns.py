from datetime import date,timedelta
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import random
import unittest
from core.optimization.lns_neighborhood import choose_neighborhood,make_rng,rotation_targets,legacy_fingerprints,NEW_CORE,Neighborhood
from core.engine.checkpoint import fingerprints
from core.data.models import MonthData,Employee,Request
from test_transitions import fixture

D=date(2026,12,1)
DAYS=tuple(D+timedelta(days=i) for i in range(31))
IDS=tuple(range(1,22))

class NeighborhoodTests(unittest.TestCase):
    def test_target_contains_focus(self):
        for d in (DAYS[0],DAYS[15],DAYS[-1]):
            nb=choose_neighborhood(IDS,DAYS,[(5,d,1)],0,random.Random(0))
            self.assertEqual(nb.kind,'target'); self.assertIn((5,d),nb.cells)
            self.assertEqual(len(nb.cells),21*9)
    def test_time_window(self):
        nb=choose_neighborhood(IDS,DAYS,[],1,random.Random(3))
        self.assertEqual(nb.kind,'time'); ds=sorted({d for _,d in nb.cells}); self.assertEqual(len(ds),8)
        self.assertEqual(ds[-1]-ds[0],timedelta(days=7)); self.assertEqual(len(nb.cells),21*8)
    def test_staff_neighborhood(self):
        nb=choose_neighborhood(IDS,DAYS,[(5,D,1)],2,random.Random(1))
        self.assertEqual(nb.kind,'staff'); self.assertEqual(len(nb.cells),6*31); self.assertIn((5,D),nb.cells)
    def test_widen(self):
        a=choose_neighborhood(IDS,DAYS,[],1,random.Random(0)); b=choose_neighborhood(IDS,DAYS,[],4,random.Random(0))
        self.assertGreater(len(b.cells),len(a.cells))
    def test_no_outside(self):
        for j in range(50):
            nb=choose_neighborhood((1,2),DAYS[:2],[(55,D,1),(1,DAYS[10],1)],j,random.Random(j))
            self.assertTrue(nb.cells<=frozenset((i,d) for i in (1,2) for d in DAYS[:2]))
    def test_single_and_empty(self):
        self.assertEqual(choose_neighborhood((1,),(D,),[(1,D,1)],2,random.Random(1)).cells,{(1,D)})
        self.assertFalse(choose_neighborhood((),(D,),[],0,random.Random(1)).cells)
    def test_seed(self):
        a={(1,D):1,(2,D):2}; b=dict(reversed(list(a.items())))
        self.assertEqual(make_rng(a).random(),make_rng(b).random())
        self.assertNotEqual(make_rng(a).random(),make_rng(a,1).random())
        self.assertNotEqual(make_rng(a).random(),make_rng({(1,D):2,(2,D):2}).random())
    def test_rotation_boundary(self):
        data=fixture(previous='2'); e=data.employees[0]
        a={(e.id,D):1,(e.id,D+timedelta(days=1)):0,(e.id,D+timedelta(days=2)):2}
        self.assertEqual(rotation_targets(data,DAYS[:3],a),[(e.id,D,2)])
    def test_legacy_exact_hash(self):
        data=MonthData(2026,12,frozenset(),(Employee(1,'A','SHIFT',True,(1,2,3)),),(),{}, {})
        with TemporaryDirectory() as td:
            root=Path(td); (root/'core').mkdir(); src=root/'core/old.py'; src.write_text('x=1')
            old=fingerprints(data,(),root)
            for name in NEW_CORE: (root/'core'/name).write_text('x=2')
            self.assertEqual(legacy_fingerprints(data,(),root),old)
            self.assertNotEqual(fingerprints(data,(),root),old)
            src.write_text('x=3'); self.assertNotEqual(legacy_fingerprints(data,(),root),old)
    def test_legacy_hard_change(self):
        data=MonthData(2026,12,frozenset(),(Employee(1,'A','SHIFT',True,(1,2,3)),),(),{}, {})
        with TemporaryDirectory() as td:
            self.assertNotEqual(legacy_fingerprints(data,(),td),legacy_fingerprints(replace(data,requests=(Request(1,D,'DT'),)),(),td))

try:
    import ortools
except ImportError:
    ortools=None

@unittest.skipIf(ortools is None,'OR-Tools chưa cài')
class ModelTests(unittest.TestCase):
    def context(self):
        from ortools.sat.python import cp_model
        from core.model.model_builder import ScheduleModel
        m=cp_model.CpModel(); ids=(1,2); ds=(D,D+timedelta(days=1)); shifts=(1,2,3)
        x={(i,d,s):m.new_bool_var(f'x{i}{d}{s}') for i in ids for d in ds for s in shifts}
        w={(i,d):m.new_bool_var(f'w{i}{d}') for i in ids for d in ds}
        for i,d in w: m.add(w[i,d]==sum(x[i,d,s] for s in shifts))
        return ScheduleModel(m,ds,ids,shifts,x,w)
    def test_clone_freeze(self):
        from core.optimization.objective_lns import clone_neighborhood
        from core.optimization.solver import solve
        ctx=self.context(); a={k:1 for k in ctx.worked}; focus=(1,D)
        trial=clone_neighborhood(ctx,a,{focus}); trial.model.add(trial.x[1,D,2]==1)
        r=solve(trial,2); self.assertEqual(r.assignments[focus],2)
        self.assertTrue(all(r.assignments[k]==1 for k in a if k!=focus))
        ctx.model.add(ctx.x[2,D,3]==1); self.assertEqual(solve(ctx,2).assignments[2,D],3)
    def test_lns_proven_bound_and_lock(self):
        from unittest.mock import patch
        from core.optimization.objective_lns import refine_lns
        from core.optimization.solver import SolveResult,solve
        ctx=self.context(); a={k:1 for k in ctx.worked}; term=ctx.model.new_int_var(0,1,'obj'); ctx.model.add(term==ctx.x[1,D,1]); score=lambda a:(int(a[1,D]==1),)
        with patch('core.optimization.objective_lns.rotation_targets',return_value=[(1,D,1)]),patch('core.optimization.objective_lns.choose_neighborhood',return_value=Neighborhood('target',frozenset({(1,D)}))):
            r,layer=refine_lns(ctx,None,SolveResult('FEASIBLE',a,0),score,(term,),term,(1,),(0,),4,0,lambda a:None,kind='rotation',fraction=.5,slice_time=1)
        self.assertEqual(layer.status,'PROVEN_BOUND'); self.assertEqual(layer.after,(0,)); self.assertEqual(layer.before,(1,)); self.assertNotEqual(r.assignments[1,D],1)
        self.assertEqual(score(solve(ctx,2).assignments),(0,))
    def test_local_infeasible_not_global_proof(self):
        from unittest.mock import patch
        from core.optimization.objective_lns import refine_lns
        from core.optimization.objective_night import Layer
        from core.optimization.solver import SolveResult
        ctx=self.context(); a={k:1 for k in ctx.worked}; term=ctx.model.new_int_var(0,1,'obj'); ctx.model.add(term==ctx.x[1,D,1]); score=lambda a:(int(a[1,D]==1),)
        incumbent=SolveResult('FEASIBLE',a,0)
        def fallback(*args): return incumbent,Layer('FEASIBLE',incumbent,(1,),(1,),'fallback')
        with patch('core.optimization.objective_lns.rotation_targets',return_value=[]),patch('core.optimization.objective_lns.choose_neighborhood',return_value=Neighborhood('staff',frozenset({(2,D)}))),patch('core.optimization.objective_lns.solve_to_bound',return_value=SolveResult('INFEASIBLE',None,0)),patch('core.optimization.objective_lns.refine_lex',side_effect=fallback) as f:
            r,layer=refine_lns(ctx,None,incumbent,score,(term,),term,(1,),(0,),4,0,lambda a:None,kind='rotation',fraction=.1,slice_time=.1)
        self.assertTrue(f.called); self.assertEqual(layer.status,'FEASIBLE'); self.assertEqual(r.assignments,a)
