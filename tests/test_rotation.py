import unittest
from dataclasses import replace
from datetime import date,timedelta
from itertools import product
from types import SimpleNamespace
from importlib.util import find_spec
from core.data.models import Employee,Request
from core.objectives.rotation_checks import rotation_rows,rotation_score,preferred_rest_minutes
from core.objectives.rest_bound_v2 import min_touched,weekend_lower_v2
from core.objectives.rest_checks import rest_windows
from test_rest_balance import fixture

HAS_ORTOOLS=find_spec('ortools') is not None

class RotationTests(unittest.TestCase):
    def blank(self):
        d,days,a=fixture(n=2)
        for k in a: a[k]=0
        for e in d.employees: d.previous[e.id,days[0]-timedelta(days=1)]='OFF'
        return d,days,a
    def test_same_type(self):
        d,days,a=self.blank(); a[1,days[0]]=a[1,days[1]]=2
        self.assertEqual(rotation_score(rotation_rows(d,days,a)),(0,0,0,0))
    def test_change_and_short(self):
        d,days,a=self.blank()
        for day,s in zip(days,(2,1,2,3)): a[1,day]=s
        self.assertEqual(rotation_score(rotation_rows(d,days,a)),(3,1,3,3))
    def test_off_breaks_pair(self):
        d,days,a=self.blank(); a[1,days[0]]=1; a[1,days[2]]=3
        self.assertEqual(rotation_score(rotation_rows(d,days,a)),(0,0,0,0))
    def test_ct_breaks_pair(self):
        d,days,a=self.blank(); a[1,days[0]]=1; a[1,days[2]]=3
        d=replace(d,requests=(Request(1,days[1],'CT'),))
        self.assertEqual(rotation_score(rotation_rows(d,days,a)),(0,0,0,0))
    def test_history(self):
        d,days,a=self.blank(); d.previous[1,days[0]-timedelta(days=1)]='3'; a[1,days[0]]=2
        self.assertEqual(rotation_score(rotation_rows(d,days,a)),(1,1,1,1))
    def test_threshold_equal_not_short(self):
        d,days,a=self.blank(); a[1,days[0]]=2; a[1,days[1]]=1
        d.rules['optimization']={'preferred_transition_rest_hours':8}
        self.assertEqual(rotation_score(rotation_rows(d,days,a)),(1,0,1,1))
    def test_invalid_threshold(self):
        d,days,a=self.blank(); d.rules['optimization']={'preferred_transition_rest_hours':-1}
        with self.assertRaises(ValueError): preferred_rest_minutes(d)
    def test_admin_excluded(self):
        d,days,a=self.blank(); d=replace(d,employees=(d.employees[0],replace(d.employees[1],group='ADMIN')))
        a[2,days[0]]=2; a[2,days[1]]=3
        self.assertEqual(len(rotation_rows(d,days,a)),1)
        self.assertEqual(rotation_score(rotation_rows(d,days,a)),(0,0,0,0))
    def test_balance_gap(self):
        d,days,a=self.blank()
        for day,s in zip(days,(1,2,3)): a[1,day]=s
        for day,s in zip(days,(2,3)): a[2,day]=s
        self.assertEqual(rotation_score(rotation_rows(d,days,a)),(3,0,2,1))

class BoundTests(unittest.TestCase):
    def sample(self,holidays=()):
        d,days,a=fixture(n=16); d=replace(d,holidays=frozenset(holidays)); d.rules['admin_support']={'allow_weekend':False}
        w=rest_windows(d,days); demand={(day,s):3 for day in days for s in (1,2,3)}
        return d,days,w,demand
    def test_normal(self):
        d,days,w,dem=self.sample()
        self.assertEqual(weekend_lower_v2(d,days,w,dem,0,SimpleNamespace(status='PROVEN_BOUND',after=(1,128))),(0,40,1,0))
    def test_holiday(self):
        d,days,w,dem=self.sample((date(2026,12,25),date(2026,12,26)))
        self.assertEqual(weekend_lower_v2(d,days,w,dem,0,SimpleNamespace(status='PROVEN_BOUND',after=(1,30))),(0,40,1,0))
    def test_disabled(self):
        d,days,w,dem=self.sample()
        self.assertEqual(weekend_lower_v2(d,days,w,dem,0,SimpleNamespace(status='DISABLED',after=(1,128))),(0,36,0,0))
    def test_incomplete_weekend(self):
        d,days,a=fixture(2027,1,n=16); d.rules['admin_support']={'allow_weekend':False}
        w=rest_windows(d,days); dem={(day,s):3 for day in days for s in (1,2,3)}
        self.assertEqual(weekend_lower_v2(d,days,w,dem,0,SimpleNamespace(status='PROVEN_BOUND',after=(1,0))),(0,36,0,0))
    def test_dp_exhaustive(self):
        for n in range(1,5):
            for t in range(0,2*n+1):
                q,r=divmod(t,n); a=[q]*(n-r)+[q+1]*r
                for H in range(3):
                    ref={}
                    for h in product(range(H+1),repeat=n):
                        val=sum((x+y+1)//2 for x,y in zip(a,h)); k=sum(h)
                        ref[k]=min(ref.get(k,val),val)
                    for k,val in ref.items(): self.assertEqual(min_touched(n,t,k,H),val)

@unittest.skipUnless(HAS_ORTOOLS,'Thiếu OR-Tools')
class SolverTests(unittest.TestCase):
    def test_model_matches_score(self):
        from core.model.model_builder import build_model
        from core.objectives.rotation_constraints import add_rotation_balance
        from ortools.sat.python import cp_model
        d,days,a=fixture(n=2)
        a[1,days[0]]=1; a[1,days[1]]=3; a[2,days[2]]=0
        ctx=build_model(d); t=add_rotation_balance(ctx,d)
        for (i,day,s),v in ctx.x.items(): ctx.model.add(v==int(a[i,day]==s))
        solver=cp_model.CpSolver(); self.assertEqual(solver.solve(ctx.model),cp_model.OPTIMAL)
        self.assertEqual(tuple(solver.value(v) for v in t.terms),rotation_score(rotation_rows(d,days,a)))
    def test_prefers_same(self):
        from core.model.model_builder import build_model
        from core.objectives.rotation_constraints import add_rotation_balance
        from core.optimization.solver import solve
        d,days,a=fixture(n=1); ctx=build_model(d); t=add_rotation_balance(ctx,d)
        for day in days: ctx.model.add(ctx.worked[1,day]==int(day in days[:2]))
        ctx.model.add(ctx.x[1,days[0],2]==1); ctx.model.minimize(t.objective)
        result=solve(ctx,10); self.assertEqual(result.status,'OPTIMAL'); self.assertEqual(result.assignments[1,days[1]],2)
    def test_lex_bound_not_componentwise(self):
        from core.model.model_builder import build_model
        from core.optimization.objective_lex_v2 import refine_lex
        from core.optimization.solver import SolveResult
        d,days,a=fixture(n=1); ctx=build_model(d); m=ctx.model
        # before=(2,0), lower=(1,1); 2nd coordinate may be smaller, but lex is valid.
        x=ctx.x[1,days[0],1]; p=m.new_int_var(1,2,'p'); q=m.new_int_var(0,1,'q')
        m.add(p==2-x); m.add(q==x)
        a[1,days[0]]=2
        score=lambda aa:(1,1) if aa[1,days[0]]==1 else (2,0)
        selected,layer=refine_lex(ctx,SolveResult('FEASIBLE',a,0),score,(p,q),2*p+q,(2,1),(1,1),5,2,lambda a:None)
        self.assertEqual(layer.after,(1,1)); self.assertEqual(layer.status,'PROVEN_BOUND')
    def test_trial_infeasible_next_integer(self):
        from core.model.model_builder import build_model
        from core.optimization.objective_lex_v2 import refine_lex
        from core.optimization.solver import SolveResult
        d,days,a=fixture(n=1); ctx=build_model(d); m=ctx.model
        p=m.new_int_var(0,1,'p'); q=m.new_int_var(0,1,'q'); m.add(p==0); m.add(q==1)
        selected,layer=refine_lex(ctx,SolveResult('FEASIBLE',a,0),lambda a:(0,1),(p,q),2*p+q,(1,1),(0,0),5,2,lambda a:None)
        self.assertEqual(layer.status,'PROVEN_BOUND'); self.assertEqual(layer.after,(0,1))
        self.assertEqual(selected.assignments,a)

if __name__=='__main__': unittest.main()
