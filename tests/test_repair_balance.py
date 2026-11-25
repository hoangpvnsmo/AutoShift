"""Real bounded-repair refinement and independent workload/guard checks."""
from test_v50 import DBCase,fixture,seed_month,ASOF
from dataclasses import replace
from datetime import date
from time import perf_counter
from copy import deepcopy
from sqlalchemy import select
from ortools.sat.python import cp_model
from core.data.models import Request
from autoshift5 import models as m
from autoshift5.cascade_model import CascadeFrame,LinkedModel
from autoshift5.cascade import inspect_candidate
from autoshift5.repair import RepairOptions
from autoshift5.repair_balance import Workload,refine_repair,balanced_proof
from autoshift5.repair_balance_context import month_record
from autoshift5.schemas import JobInput,RequestBatch,ConfirmInput
from autoshift5.worker import execute
from autoshift5.cascade_service import confirm_cascade
from autoshift5.domain import CODE_ID,Conflict


def example():
    data,codes=fixture();data=replace(data,requests=(Request(1,date(2028,1,3),'BKD'),))
    repaired=dict(codes);repaired[1,date(2028,1,3)]='BKD';repaired[3,date(2028,1,3)]='1'
    return CascadeFrame(1,data,(),codes,None),repaired


class RefinementTests(DBCase):
    def test_cp_matches_independent_annual_monthly_and_night_metrics(self):
        frame,codes=example();data,prior=fixture(2028,2)
        context={'months':[month_record(data,prior)]}
        ledger=Workload([frame],context);model=LinkedModel([frame],None,RepairOptions(as_of=ASOF),quality=False)
        terms=ledger.terms(model)
        for key,var in model.values.items():model.model.add(var==CODE_ID[codes[key]])
        solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=5
        self.assertEqual(solver.solve(model.model),cp_model.OPTIMAL)
        self.assertEqual(tuple(solver.value(t) for label,t in terms),ledger.score(codes))
        self.assertEqual(ledger.groups[0]['observed_months'],2)
        self.assertEqual(len(ledger.groups),4)

    def test_real_refinement_improves_load_without_more_short_rest_or_resources(self):
        frame,codes=example();options=RepairOptions(as_of=ASOF)
        before=inspect_candidate([frame],None,codes,options,direct_ids={1})[2]
        entries,_,metrics,report=refine_repair([frame],None,codes,options,direct_ids={1},deadline=perf_counter()+12)
        ledger=Workload([frame],None)
        self.assertLess(ledger.score(entries[0]['codes']),ledger.score(codes),report['layers'])
        self.assertEqual(report['status'],'IMPROVED')
        self.assertLessEqual(metrics['changed_cells'],before['changed_cells']+2)
        self.assertLessEqual(metrics['vector'][4],before['vector'][4])
        self.assertTrue(all(a<=b for a,b in zip(metrics['vector'][3],before['vector'][3])))
        self.assertEqual(entries[0]['codes'][1,date(2028,1,3)],'BKD')

    def test_cp_workload_totals_follow_new_demand_instead_of_old_baseline(self):
        data,codes=fixture();rules=deepcopy(data.rules)
        rules['credit_policy']['daily_demand']={'2028-01-03':{'1':4}}
        data=replace(data,rules=rules);frame=CascadeFrame(1,data,(),codes,None)
        repaired=dict(codes);repaired[3,date(2028,1,3)]='1'
        options=RepairOptions(as_of=ASOF);inspect_candidate([frame],None,repaired,options)
        ledger=Workload([frame],None);linked=LinkedModel([frame],None,options,quality=False)
        terms=ledger.terms(linked)
        for key,var in linked.values.items():linked.model.add(var==CODE_ID[repaired[key]])
        solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=5
        self.assertEqual(solver.solve(linked.model),cp_model.OPTIMAL)
        self.assertEqual(tuple(solver.value(t) for label,t in terms),ledger.score(repaired))

    def test_max_changes_and_locked_cells_remain_hard_limits(self):
        frame,codes=example();locked=(3,date(2028,1,1));frame=replace(frame,locks=(locked,))
        options=RepairOptions(as_of=ASOF,max_changes=2)
        entries,_,metrics,report=refine_repair([frame],None,codes,options,deadline=perf_counter()+5,direct_ids={1})
        self.assertLessEqual(metrics['changed_cells'],2);self.assertEqual(report['change_limit'],2)
        self.assertEqual(entries[0]['codes'][locked],frame.baseline[locked])

    def test_no_change_expired_budget_and_cancel_keep_validated_fallback(self):
        data,codes=fixture();frame=CascadeFrame(1,data,(),codes,None)
        _,_,metrics,r=refine_repair([frame],None,codes,RepairOptions(as_of=ASOF),deadline=perf_counter()+1)
        self.assertEqual(r['status'],'NO_CHANGE_NEEDED');self.assertEqual(metrics['changed_cells'],0)
        frame,codes=example()
        for kwargs,status in [({'deadline':perf_counter()-1},'NOT_RUN_BUDGET_EXHAUSTED'),({'deadline':perf_counter()+5,'cancelled':lambda:True},'CANCELLED')]:
            entries,_,_,r=refine_repair([frame],None,codes,RepairOptions(as_of=ASOF),**kwargs)
            self.assertEqual(entries[0]['codes'],codes);self.assertEqual(r['status'],status)

    def test_outside_year_counts_are_fixed_and_missing_months_not_invented(self):
        frame,codes=example();other,oc=fixture(2027,12);same,sc=fixture(2028,2)
        context={'months':[month_record(other,oc),month_record(same,sc)]}
        untouched=deepcopy(context);ledger=Workload([frame],context)
        self.assertEqual(ledger.groups[0]['observed_months'],2)
        self.assertTrue(all(g['year']==2028 for g in ledger.groups))
        report=ledger.report(codes)[0]
        self.assertEqual(report['counts'][3],sum(c in ('1','2','3') for (i,d),c in sc.items() if i==3)+17)
        self.assertEqual(context,untouched)
        nights=next(g for g in ledger.groups if g['field']=='night')
        self.assertEqual(set(nights['weights']),set(range(1,7)))

    def test_extra_change_proof_retains_base_certificate_without_false_r1_claim(self):
        proof={'status':'R1_GLOBAL_OPTIMAL','r1_global_optimal':True,'r1_certificate':{'value':2}}
        report=dict(status='IMPROVED',base_changes=2,selected_changes=4,change_limit=4,extra_changes=2,base_short_rest=0,selected_short_rest=0)
        p=balanced_proof(proof,report)
        self.assertFalse(p['r1_global_optimal']);self.assertNotIn('r1_certificate',p)
        self.assertTrue(p['stable_repair_proof']['r1_global_optimal']);self.assertTrue(proof['r1_global_optimal'])
        same={**report,'selected_changes':2,'extra_changes':0}
        p=balanced_proof({**proof,'layers':[{'layer':'R2','value':1}]},same)
        self.assertTrue(p['r1_global_optimal']);self.assertNotIn('layers',p)
        self.assertIn('layers',p['stable_repair_proof'])

    def test_unsupported_frontier_hours_keep_the_stable_repair(self):
        frame,codes=example();data,other=fixture(2028,2);rules=deepcopy(data.rules)
        rules['shift_hours']['2']['start']='07:00';rules['shift_hours']['2']['end']='15:00'
        data=replace(data,rules=rules);right=CascadeFrame(2,data,(),other,None,fixed_data=data)
        entries,_,_,report=refine_repair([frame],right,codes,RepairOptions(as_of=ASOF),deadline=perf_counter()+5)
        self.assertEqual(report['status'],'NOT_RUN_UNSUPPORTED');self.assertEqual(entries[0]['codes'],codes)


class RepairWorkerTests(DBCase):
    def prepare(self):
        with self.service.sessions.begin() as s:
            mid=seed_month(s,*fixture())
            second=seed_month(s,*fixture(2028,2))
            old=s.get(m.PlanningMonth,mid).current_version_id
        req=self.service.request_batch(RequestBatch(revision=1,employee_ids=[1],from_date=date(2028,1,3),to_date=date(2028,1,3),code='BKD'))
        return mid,second,old,req

    def test_local_worker_records_context_summary_and_leaves_live_baseline(self):
        mid,second,old,req=self.prepare()
        j=self.service.enqueue(JobInput(revision=req['revision'],mode='REPAIR',month_id=mid,as_of=ASOF,budget=16,
            request_ids=[r['id'] for r in req['requests']]))
        self.assertEqual(len(j['options']['repair_balance_context']['months']),2)
        execute(self.engine,j['id']);result=self.service.job(j['id'])
        self.assertEqual(result['status'],'SUCCEEDED',result['error'])
        r=result['result']['repair_balance'];self.assertLessEqual(r['selected_changes'],r['change_limit'])
        self.assertLessEqual(r['selected_short_rest'],r['base_short_rest'])
        self.assertEqual(self.service.calendar(mid)['month']['current_version_id'],old)
        self.assertEqual(result['result']['publication'],'DRAFT_ONLY')

    def test_linked_worker_confirms_atomically(self):
        mid,second,old,req=self.prepare()
        j=self.service.enqueue(JobInput(revision=req['revision'],mode='REPAIR',month_id=mid,as_of=ASOF,budget=16,cascade=True,
            request_ids=[r['id'] for r in req['requests']]))
        execute(self.engine,j['id']);result=self.service.job(j['id'])
        self.assertEqual(result['status'],'SUCCEEDED',result['error']);self.assertIn('repair_balance',result['result'])
        confirm_cascade(self.service,j['id'],ConfirmInput(revision=req['revision'],expected_current_version_id=old))
        self.assertEqual(self.service.calendar(mid)['month']['current_version_id'],result['result']['version_ids'][0])
        with self.assertRaises(Conflict):confirm_cascade(self.service,j['id'],ConfirmInput(revision=req['revision'],expected_current_version_id=old))

    def test_changed_inputs_in_a_context_month_block_stale_confirmation(self):
        mid,second,old,req=self.prepare()
        j=self.service.enqueue(JobInput(revision=req['revision'],mode='REPAIR',month_id=mid,as_of=ASOF,budget=12,cascade=True,cascade_months=0,
            request_ids=[r['id'] for r in req['requests']]))
        execute(self.engine,j['id']);result=self.service.job(j['id'])
        self.assertEqual(result['status'],'SUCCEEDED',result['error'])
        later=self.service.request_batch(RequestBatch(revision=req['revision'],employee_ids=[2],from_date=date(2028,2,3),to_date=date(2028,2,3),code='BKD'))
        with self.assertRaises(Conflict):confirm_cascade(self.service,j['id'],ConfirmInput(revision=later['revision'],expected_current_version_id=old))
        self.assertEqual(self.service.calendar(mid)['month']['current_version_id'],old)

    def test_disabled_balance_preserves_old_pipeline(self):
        mid,second,old,req=self.prepare()
        j=self.service.enqueue(JobInput(revision=req['revision'],mode='REPAIR',month_id=mid,as_of=ASOF,budget=10,
            repair_balance=False,request_ids=[r['id'] for r in req['requests']]))
        self.assertNotIn('repair_balance_context',j['options'])
        execute(self.engine,j['id']);result=self.service.job(j['id'])
        self.assertEqual(result['status'],'SUCCEEDED',result['error']);self.assertNotIn('repair_balance',result['result'])
