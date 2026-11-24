import unittest
from datetime import date,timedelta
from dataclasses import replace
from importlib.util import find_spec
from core.data.models import Employee,MonthData
from core.objectives.night_checks import night_groups,night_rows,night_score,night_lower_bound,NightGroupRow
from core.objectives.quality_checks import shift_balance_score,shift_balance_rows

HAS_ORTOOLS=find_spec('ortools') is not None


def fixture():
    return MonthData(2026,12,frozenset(),(
        Employee(1,'S1','SHIFT',True,(1,2,3),23),
        Employee(2,'S2','SHIFT',False,(1,2,3),23),
        Employee(3,'A','ADMIN',False,(2,3))),(),{}, {'max_shifts_per_day':1})


def make_assignments(data,days,counts):
    a={(e.id,d):0 for e in data.employees for d in days}
    for i,values in counts.items():
        k=0
        for s,n in enumerate(values,1):
            for d in days[k:k+n]: a[i,d]=s
            k+=n
    return a


class NightCheckTests(unittest.TestCase):
    def test_same_e01_different_e02(self):
        data=fixture(); days=tuple(date(2026,12,1)+timedelta(days=i) for i in range(31))
        a=make_assignments(data,days,{1:(7,6,6),2:(5,6,6)})
        b=make_assignments(data,days,{1:(6,7,6),2:(6,5,6)})
        self.assertEqual(shift_balance_score(shift_balance_rows(data,days,a)),(1,2))
        self.assertEqual(shift_balance_score(shift_balance_rows(data,days,b)),(1,2))
        self.assertEqual(night_score(night_rows(data,days,a)),(2,2))
        self.assertEqual(night_score(night_rows(data,days,b)),(0,0))
        for s in (1,2,3): self.assertEqual(sum(v==s for v in a.values()),sum(v==s for v in b.values()))

    def test_groups(self):
        data=fixture(); data=replace(data,employees=data.employees+(
            Employee(4,'S4','SHIFT',False,(2,3),23),
            Employee(5,'S5','SHIFT',False,(1,2),23),
            Employee(6,'S6','SHIFT',False,(1,2,3),17),
            Employee(7,'S7','SHIFT',False,(3,2,1),None)))
        self.assertEqual(night_groups(data),{((1,2),23):(5,),((1,2,3),17):(6,),((1,2,3),23):(1,2,7)})

    def test_sample_lower_bound(self):
        data=fixture(); data=replace(data,employees=tuple(Employee(i,str(i),'SHIFT',False,(1,2,3),23) for i in range(1,17)))
        self.assertEqual(night_lower_bound(data,{(date(2026,12,i),1):3 for i in range(1,32)}),(1,1))

    def test_divisible_bound(self):
        self.assertEqual(night_lower_bound(fixture(),{(date(2026,12,1),1):2}),(0,0))

    def test_multiple_groups_bound(self):
        data=fixture(); data=replace(data,employees=(data.employees[0],replace(data.employees[1],target_credit=17)))
        self.assertEqual(night_lower_bound(data,{(date(2026,12,1),1):1}),(0,0))

    def test_zero_groups(self):
        data=fixture(); data=replace(data,employees=(replace(data.employees[0],allowed_shifts=(2,3)),data.employees[2]))
        self.assertEqual(night_groups(data),{}); self.assertEqual(night_score([]),(0,0))

    def test_group_scores(self):
        rows=[NightGroupRow((1,2,3),23,((1,8),(2,5))),NightGroupRow((1,2),23,((3,4),(4,2))),NightGroupRow((1,),23,((5,7),))]
        self.assertEqual(night_score(rows),(3,5))

    def test_counts_only_night(self):
        data=fixture(); days=(date(2026,12,1),date(2026,12,2))
        a=make_assignments(data,days,{1:(1,1,0),2:(0,0,1),3:(0,1,1)})
        self.assertEqual(night_rows(data,days,a)[0].counts,((1,1),(2,0)))


@unittest.skipUnless(HAS_ORTOOLS,'Thiếu OR-Tools')
class NightSolverTests(unittest.TestCase):
    def test_e02_improves_same_e01(self):
        from core.model.model_builder import build_model
        from core.objectives.soft_constraints import add_shift_balance
        from core.objectives.night_constraints import add_night_balance
        from core.optimization.solver import solve
        data=fixture(); ctx=build_model(data); m=ctx.model
        q=add_shift_balance(ctx,data)
        choose=m.new_bool_var('choose_balanced')
        # A vs B: cùng tổng từng loại ca và cùng E01, khác số ca đêm.
        for i,s,expr in [(1,1,7-choose),(1,2,6+choose),(1,3,6),(2,1,5+choose),(2,2,6-choose),(2,3,6)]:
            m.add(q.counts[i,s]==expr)
        m.add(q.worst_gap==1); m.add(q.total_gap==2)
        m.add(sum(ctx.worked[3,d] for d in ctx.days)==0)
        n=add_night_balance(ctx,data,q.counts)
        m.minimize(n.objective); r=solve(ctx,10)
        self.assertEqual(r.status,'OPTIMAL'); self.assertEqual(night_score(night_rows(data,ctx.days,r.assignments)),(0,0))
        self.assertEqual(shift_balance_score(shift_balance_rows(data,ctx.days,r.assignments)),(1,2))

    def test_forced_imbalance_soft(self):
        from core.model.model_builder import build_model
        from core.objectives.night_constraints import add_night_balance
        from core.optimization.solver import solve
        data=fixture(); ctx=build_model(data); n=add_night_balance(ctx,data)
        ctx.model.add(n.counts[1]==5); ctx.model.add(n.counts[2]==1)
        ctx.model.minimize(n.objective); r=solve(ctx,10)
        self.assertEqual(r.status,'OPTIMAL'); self.assertEqual(night_score(night_rows(data,ctx.days,r.assignments)),(4,4))

    def test_no_groups(self):
        from core.model.model_builder import build_model
        from core.objectives.night_constraints import add_night_balance
        from core.optimization.solver import solve
        data=fixture(); data=replace(data,employees=(replace(data.employees[0],allowed_shifts=(2,3)),data.employees[2]))
        ctx=build_model(data); n=add_night_balance(ctx,data); ctx.model.minimize(n.objective)
        self.assertEqual(solve(ctx,10).status,'OPTIMAL')

    def test_refine_timeout(self):
        from unittest.mock import patch,Mock
        from core.optimization.objective_night import refine
        from core.optimization.solver import SolveResult
        base=SolveResult('OPTIMAL',{(1,date(2026,12,1)):1},0.1)
        ctx=Mock(); ctx.x={}; ctx.worked={}
        with patch('core.optimization.objective_night.solve',return_value=SolveResult('UNKNOWN',None,0.1)):
            selected,layer=refine(ctx,base,lambda a:(1,1),0,1,lambda a:None)
        self.assertIs(selected,base); self.assertEqual(layer.before,layer.after)
        ctx.model.clear_hints.assert_called_once()

    def test_integration_two_scenarios(self):
        from pathlib import Path
        from copy import deepcopy
        from core.data.data_loader import load_data
        from core.model.engine_model import build_stage3
        from core.optimization.objective_night import solve_quality42
        from core.validation.admin_checks import check_admin_credits
        from core.validation.credit_checks import check_staffing
        from core.validation.sequence_checks import check_sequences
        from core.validation.transition_checks import check_transitions
        from core.objectives.quality_checks import workforce_score
        root=Path(__file__).resolve().parents[1]
        source=load_data(root/'tests/fixtures/input/input.xlsx',root/'tests/fixtures/config/rules.json')
        if source.rules.get('shift_hours') is None:
            source.rules['shift_hours']={str(s):{'start':a,'end':b,'start_day_offset':o} for s,a,b,o in [(1,'22:00','06:00',-1),(2,'06:00','14:00',0),(3,'14:00','22:00',0)]}
        for demo in (None,17):
            with self.subTest(demo=demo):
                data=deepcopy(source)
                if demo is not None: data=replace(data,employees=tuple(replace(e,target_credit=demo) if e.group=='SHIFT' else e for e in data.employees))
                data.rules.setdefault('optimization',{}).update(balance_shift_types=True,balance_night_shifts=True)
                ctx,demand,_,credit=build_stage3(data)
                q=solve_quality42(ctx,data,credit,demand,30,60,30)
                a=q.selected.assignments; self.assertIsNotNone(a)
                err,_,rows=check_admin_credits(data,ctx.days,a)
                self.assertFalse(err); self.assertFalse(check_staffing(data,ctx.days,a)); self.assertFalse(check_sequences(data,ctx.days,a)[0]); self.assertFalse(check_transitions(data,ctx.days,a)[0])
                self.assertEqual(workforce_score(rows),q.workforce)
                self.assertEqual(shift_balance_score(shift_balance_rows(data,ctx.days,a)),q.e01.after)
                self.assertLessEqual(q.e01.after,q.e01.before); self.assertLessEqual(q.e02.after,q.e02.before)
                self.assertEqual(night_score(night_rows(data,ctx.days,a)),q.e02.after)

if __name__=='__main__': unittest.main()
