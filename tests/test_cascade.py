"""Real linked CP-SAT, independent boundary validation, atomic publication."""
from test_v50 import DBCase, fixture, seed_month, ASOF
from dataclasses import replace
from datetime import date
from sqlalchemy import select, func
from ortools.sat.python import cp_model
from fastapi.testclient import TestClient
from core.data.models import Request
from autoshift5.api import create_app
from autoshift5 import models as m
from autoshift5.schemas import JobInput, RequestBatch, ConfirmInput, LockInput
from autoshift5.worker import execute
from autoshift5.cascade_service import confirm_cascade
from autoshift5.cascade_model import CascadeFrame, LinkedModel
from autoshift5.cascade import inspect_candidate, solve_cascade
from autoshift5.repair import RepairOptions
from autoshift5.repository import codes_for, require
from autoshift5.domain import CODE_ID, Conflict
from autoshift5.db import revision


class LinkedModelTests(DBCase):
    def test_full_code_quality_matches_independent_across_variable_seam(self):
        frames=[]
        for month in (1,2):
            d,c=fixture(2028,month)
            people=tuple(replace(e,allowed_shifts=(1,2,3)) for e in d.employees)
            requests=[]
            for i,code in ((1,'CT'),(2,'BKD')):
                day=next(day for (eid,day),old in c.items() if eid==i and old=='OFF')
                c[i,day]=code;requests.append(Request(i,day,code))
            d=replace(d,employees=people,requests=tuple(requests))
            frames.append(CascadeFrame(month,d,(),c,None))
        options=RepairOptions(as_of=ASOF)
        model=LinkedModel(frames,None,options)
        flat={k:c for f in frames for k,c in f.baseline.items()}
        for key,var in model.values.items():model.model.add(var==CODE_ID[flat[key]])
        solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=5
        self.assertEqual(solver.solve(model.model),cp_model.OPTIMAL)
        entries,boundaries,metrics=inspect_candidate(frames,None,flat,options)
        expected=[int(solver.value(g[0])) if j!=3 else [int(solver.value(t)) for t in g] for j,g in enumerate(model.groups)]
        expected.append([[[int(solver.value(t)) for t in g] for g in q] for q in model.qualities])
        self.assertEqual(expected,metrics['vector'])
        self.assertEqual(boundaries[0]['errors'],0)
        self.assertEqual(entries[0]['codes'],frames[0].baseline)

    def test_cancellation_does_not_report_infeasible(self):
        d,c=fixture();f=CascadeFrame(1,d,(),c,None)
        r=solve_cascade([f],None,RepairOptions(as_of=ASOF),cancelled=lambda:True)
        self.assertEqual(r.status,'CANCELLED');self.assertFalse(r.entries)


class CascadeIntegrationTests(DBCase):
    def prepare(self):
        mids=[];original=[]
        with self.service.sessions.begin() as s:
            for month in (1,2,3):
                d,c=fixture(2028,month);mid=seed_month(s,d,c);mids.append(mid)
                original.append(s.get(m.PlanningMonth,mid).current_version_id)
        req=self.service.request_batch(RequestBatch(revision=1,employee_ids=[1],from_date=date(2028,1,29),to_date=date(2028,1,31),code='1'))
        return mids,original,req

    def run_linked(self,mids,req,**kw):
        job=self.service.enqueue(JobInput(revision=req['revision'],mode='FRESH',month_id=mids[0],
            cascade=True,cascade_months=1,budget=18,**kw))
        execute(self.engine,job['id']);j=self.service.job(job['id'])
        self.assertEqual(j['status'],'SUCCEEDED',j['error']+'\n'+j['log'] if j['error'] else j['log'])
        return j

    def test_fresh_repairs_next_minimally_protects_third_and_confirms_atomically(self):
        mids,original,req=self.prepare();j=self.run_linked(mids,req)
        self.assertEqual(j['result']['repair']['changed_cells'],2)
        self.assertTrue(j['result']['proof']['r1_global_optimal'])
        self.assertEqual(len(j['result']['version_ids']),2)
        self.assertEqual(len(j['result']['boundaries']),2)
        self.assertTrue(all(b['errors']==0 for b in j['result']['boundaries']))
        with self.service.sessions() as s:
            self.assertEqual([s.get(m.PlanningMonth,x).current_version_id for x in mids],original)
            for v in j['result']['version_ids']:self.assertEqual(s.get(m.ScheduleVersion,v).status,'DRAFT')
        payload=ConfirmInput(revision=req['revision'],expected_current_version_id=original[0])
        with self.assertRaises(Conflict):self.service.confirm(j['result']['version_ids'][0],payload)
        with TestClient(create_app(self.path,start_workers=False)) as client:
            validation=client.post('/api/versions/'+j['result']['version_ids'][0]+'/validate',json={})
            self.assertEqual(validation.status_code,200,validation.text)
            self.assertTrue(all(b['errors']==0 for b in validation.json()['boundaries']))
            response=client.post('/api/jobs/'+j['id']+'/confirm',json=payload.model_dump(mode='json'))
            self.assertEqual(response.status_code,200,response.text)
            result=response.json()
        self.assertEqual(result['status'],'CONFIRMED')
        with self.service.sessions() as s:
            self.assertEqual([s.get(m.PlanningMonth,x).current_version_id for x in mids],j['result']['version_ids']+[original[2]])
            self.assertEqual(codes_for(s,original[1]),fixture(2028,2)[1])
            self.assertEqual(s.get(m.PlanningMonth,mids[1]).baseline_version_id,original[1])
            self.assertEqual(s.get(m.Request,req['requests'][0]['id']).status,'APPLIED')

    def test_zero_horizon_and_locked_required_cell_never_save_partial(self):
        mids,original,req=self.prepare()
        job=self.service.enqueue(JobInput(revision=req['revision'],mode='FRESH',month_id=mids[0],cascade=True,cascade_months=0,budget=4))
        execute(self.engine,job['id'])
        self.assertEqual(self.service.job(job['id'])['status'],'INFEASIBLE')
        locked=self.service.lock(LockInput(revision=req['revision'],month_id=mids[1],employee_ids=[1],day=date(2028,2,1),locked=True))
        job=self.service.enqueue(JobInput(revision=locked['revision'],mode='FRESH',month_id=mids[0],cascade=True,cascade_months=1,budget=5))
        execute(self.engine,job['id'])
        self.assertEqual(self.service.job(job['id'])['status'],'INFEASIBLE')
        with self.service.sessions() as s:
            self.assertEqual(s.scalar(select(func.count()).select_from(m.ScheduleVersion)),3)
            self.assertEqual([s.get(m.PlanningMonth,x).current_version_id for x in mids],original)

    def test_changed_revision_rejects_entire_approval(self):
        mids,original,req=self.prepare();j=self.run_linked(mids,req)
        updated=self.service.lock(LockInput(revision=req['revision'],month_id=mids[1],employee_ids=[1],day=date(2028,2,1),locked=True))
        with self.assertRaises(Conflict):confirm_cascade(self.service,j['id'],ConfirmInput(revision=updated['revision'],expected_current_version_id=original[0]))
        with self.service.sessions() as s:
            self.assertEqual([s.get(m.PlanningMonth,x).current_version_id for x in mids],original)
            self.assertTrue(all(s.get(m.ScheduleVersion,v).status=='DRAFT' for v in j['result']['version_ids']))
            self.assertEqual(revision(s),updated['revision'])

    def test_repair_counts_root_and_future_and_respects_change_limit(self):
        mids,original,req=self.prepare()
        body=dict(revision=req['revision'],mode='REPAIR',month_id=mids[0],baseline_version_id=original[0],
            request_ids=[r['id'] for r in req['requests']],as_of=ASOF,cascade=True,cascade_months=1,budget=14)
        job=self.service.enqueue(JobInput(**body,max_changes=0));execute(self.engine,job['id'])
        self.assertEqual(self.service.job(job['id'])['status'],'INFEASIBLE')
        job=self.service.enqueue(JobInput(**body));execute(self.engine,job['id']);j=self.service.job(job['id'])
        self.assertEqual(j['status'],'SUCCEEDED',j['error'])
        self.assertTrue(j['result']['repair']['root_counted'])
        self.assertEqual(j['result']['repair']['changed_cells'],sum(x['changed_cells'] for x in j['result']['month_summaries']))
        self.assertGreater(j['result']['month_summaries'][0]['changed_cells'],0)
        self.assertEqual(j['result']['month_summaries'][1]['changed_cells'],2)

    def test_window_expands_through_two_following_months(self):
        mids,original,req=self.prepare()
        with self.service.sessions.begin() as s:
            d,c=fixture(2028,4);fourth=seed_month(s,d,c);old_fourth=s.get(m.PlanningMonth,fourth).current_version_id
        req=self.service.request_batch(RequestBatch(revision=req['revision'],employee_ids=[1],
            from_date=date(2028,2,27),to_date=date(2028,2,29),code='1'))
        job=self.service.enqueue(JobInput(revision=req['revision'],mode='FRESH',month_id=mids[0],cascade=True,cascade_months=2,budget=20))
        execute(self.engine,job['id']);j=self.service.job(job['id'])
        self.assertEqual(j['status'],'SUCCEEDED',j['error'])
        self.assertEqual(len(j['result']['version_ids']),3)
        self.assertEqual(j['result']['attempts'][0]['months'],2)
        self.assertEqual(j['result']['attempts'][0]['status'],'INFEASIBLE')
        self.assertEqual(j['result']['month_summaries'][2]['changed_cells'],2)
        self.assertTrue(all(b['errors']==0 for b in j['result']['boundaries']))
        with self.service.sessions() as s:self.assertEqual(s.get(m.PlanningMonth,fourth).current_version_id,old_fourth)
