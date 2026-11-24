from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
import random
import unittest
from core.optimization.adaptive_policy import AdaptivePolicy, ADAPTIVE_CORE, previous_fingerprints
from core.optimization.lns_neighborhood import NEW_CORE, choose_neighborhood, Neighborhood
from core.engine.checkpoint import fingerprints
from core.data.models import MonthData, Employee, Request

D=date(2026,12,1)

class PolicyTests(unittest.TestCase):
    def test_explore_each(self):
        p=AdaptivePolicy(60); rng=random.Random(1)
        for mode in ('target','time','staff'):
            self.assertEqual(p.select(rng),mode); p.record(mode,(4,),(4,),1)
    def test_no_target(self):
        p=AdaptivePolicy(60); self.assertEqual(p.select(random.Random(0),False),'time')
    def test_extend_budget_and_reserve(self):
        p=AdaptivePolicy(60); self.assertEqual(p.remaining_local(0),30)
        for _ in range(20): p.record('target',(4,),(3,),1)
        self.assertAlmostEqual(p.remaining_local(0),48)
        self.assertAlmostEqual(p.limit-p.remaining_local(0),12)
    def test_high_fraction_capped(self):
        self.assertEqual(AdaptivePolicy(60,.9).cap,.8)
    def test_stall(self):
        p=AdaptivePolicy(60)
        for _ in range(5): p.record('staff',(4,),(4,),1)
        self.assertEqual(p.remaining_local(5),0)
        self.assertIn('không cải thiện',p.reason(5))
    def test_improve_resets_stall_and_lex(self):
        p=AdaptivePolicy(60)
        for _ in range(4): p.record('time',(4,1),(4,1),1)
        p.record('time',(4,1),(3,9),1)
        self.assertEqual(p.stall,0); self.assertEqual(p.stats['time'].primary_gain,1)
        p.record('staff',(3,9),(3,8),1)
        self.assertEqual(p.improvements,2)
    def test_weight_prefers_productive(self):
        p=AdaptivePolicy(60)
        for _ in range(3):
            p.record('target',(4,),(3,),1); p.record('staff',(4,),(4,),5)
        self.assertGreater(p.stats['target'].weight(p.slice),p.stats['staff'].weight(p.slice))
    def test_widen_and_mode(self):
        p=AdaptivePolicy(60); j=p.iteration('time')
        for _ in range(4): p.record('staff',(4,),(4,),1)
        self.assertGreater(p.iteration('time'),j); self.assertEqual(p.iteration('time')%3,1)
    def test_remaining_and_invalid(self):
        p=AdaptivePolicy(60); self.assertEqual(p.remaining_local(35),0)
        for f in (0,float('nan'),1):
            with self.assertRaises(ValueError): AdaptivePolicy(60,f)
        with self.assertRaises(ValueError): p.record('time',(2,),(3,),1)
    def test_hash_migration_exact(self):
        data=MonthData(2026,12,frozenset(),(Employee(1,'A','SHIFT',True,(1,2,3)),),(),{}, {})
        with TemporaryDirectory() as td:
            root=Path(td); (root/'core').mkdir(); (root/'core'/'old.py').write_text('a=1')
            h472=fingerprints(data,(),root)
            for n in NEW_CORE: (root/'core'/n).write_text('a=2')
            h473=fingerprints(data,(),root)
            for n in ADAPTIVE_CORE: (root/'core'/n).write_text('a=3')
            self.assertEqual(previous_fingerprints(data,(),root,'472'),h472)
            self.assertEqual(previous_fingerprints(data,(),root,'473'),h473)
            self.assertNotEqual(fingerprints(data,(),root),h473)
            (root/'core'/'old.py').write_text('a=9')
            self.assertNotEqual(previous_fingerprints(data,(),root,'473'),h473)
            self.assertNotEqual(previous_fingerprints(replace(data,requests=(Request(1,D,'DT'),)),(),root,'473'),h473)

try:
    import ortools
except ImportError:
    ortools=None

@unittest.skipIf(ortools is None,'OR-Tools chưa cài')
class AdaptiveModelTests(unittest.TestCase):
    def test_reach_bound_and_lock(self):
        from test_lns import ModelTests
        from core.optimization.objective_alns import refine_alns
        from core.optimization.solver import SolveResult,solve
        from unittest.mock import patch
        ctx=ModelTests().context(); a={k:1 for k in ctx.worked}
        term=ctx.model.new_int_var(0,1,'obj'); ctx.model.add(term==ctx.x[1,D,1]); score=lambda a:(int(a[1,D]==1),)
        with patch('core.optimization.objective_alns.rotation_targets',return_value=[(1,D,1)]),patch('core.optimization.objective_alns.choose_neighborhood',return_value=Neighborhood('target',frozenset({(1,D)}))):
            r,l=refine_alns(ctx,None,SolveResult('FEASIBLE',a,0),score,(term,),term,(1,),(0,),4,0,lambda a:None,kind='rotation',fraction=.5,slice_time=1)
        self.assertEqual(l.status,'PROVEN_BOUND'); self.assertEqual(score(solve(ctx,2).assignments),(0,))
    def test_local_infeasible_not_proof(self):
        from test_lns import ModelTests
        from core.optimization.objective_alns import refine_alns
        from core.optimization.objective_night import Layer
        from core.optimization.solver import SolveResult
        from unittest.mock import patch
        ctx=ModelTests().context(); a={k:1 for k in ctx.worked}; incumbent=SolveResult('FEASIBLE',a,0)
        term=ctx.model.new_int_var(0,1,'obj'); ctx.model.add(term==ctx.x[1,D,1]); score=lambda a:(int(a[1,D]==1),)
        with patch('core.optimization.objective_alns.rotation_targets',return_value=[]),patch('core.optimization.objective_alns.choose_neighborhood',return_value=Neighborhood('staff',frozenset({(2,D)}))),patch('core.optimization.objective_alns.solve_to_bound',return_value=SolveResult('INFEASIBLE',None,0)),patch('core.optimization.objective_alns.refine_lex',return_value=(incumbent,Layer('FEASIBLE',incumbent,(1,),(1,),'fallback'))) as f:
            r,l=refine_alns(ctx,None,incumbent,score,(term,),term,(1,),(0,),4,0,lambda a:None,kind='rotation',fraction=.5,slice_time=1)
        self.assertTrue(f.called); self.assertEqual(l.status,'FEASIBLE'); self.assertEqual(r.assignments,a)
