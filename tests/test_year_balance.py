"""Real annual exchanges/CP, independent metrics, frozen cells and atomic drafts."""
from test_v50 import DBCase,fixture,seed_month,ASOF
from dataclasses import replace
from datetime import date,timedelta
from pathlib import Path
from time import perf_counter
from sqlalchemy import select
from ortools.sat.python import cp_model
from fastapi.testclient import TestClient
from core.data.models import Request,Employee
from core.data.calendar_utils import month_days
from autoshift5 import models as m
from autoshift5.domain import CODE_ID,Conflict
from autoshift5.db import revision
from autoshift5.schemas import JobInput,ConfirmInput,LockInput
from autoshift5.cascade_model import CascadeFrame,LinkedModel,previous_with
from autoshift5.cascade_service import confirm_cascade
from autoshift5.year_metrics import YearMetrics
from autoshift5.year_balance import ExchangeSearch,balance_year
from autoshift5.year_balance_cp import add_terms,refine_windows
from autoshift5.repair import RepairOptions
from autoshift5.validation import validate_month
from autoshift5.repository import codes_for,snapshot,save_version,load_month
from autoshift5.worker import execute
from autoshift5.annual import annual_statistics
from autoshift5.api import create_app
from autoshift5.reporting import export_annual


def frames_for(months=(1,2,3),year=2028):
    frames=[]
    for month in months:
        data,codes=fixture(year,month)
        data=replace(data,employees=tuple(replace(e,allowed_shifts=(1,2,3)) for e in data.employees))
        frames.append(CascadeFrame(month,data,(),codes,None))
    return frames


def skewed_frames():
    frames=frames_for();ctx=YearMetrics(frames);search=ExchangeSearch(ctx,RepairOptions(as_of=ASOF))
    # Build a declared valid but unequal baseline using actual hard checks.
    changed=0
    for d in ctx.days:
        donor,receiver=13,14
        if search.codes[donor,d]=='3' and search.codes[receiver,d]=='OFF':
            proposal=search.exchange(donor,receiver,[d])
            if proposal and search.valid(proposal):search.codes.update(proposal);changed+=1
        if changed>=5:break
    if changed<2:raise AssertionError('Skew fixture did not create enough valid transfers.')
    result=[]
    for f in frames:
        codes={k:search.codes[k] for k in f.baseline}
        data=previous_with(f.data,search.codes);validate_month(data,codes)
        result.append(replace(f,data=data,baseline=codes))
    return result


class YearMetricsTests(DBCase):
    def test_reporting_and_export_include_admin_with_uniform_columns(self):
        with self.service.sessions.begin() as s:
            plan=m.AnnualPlan(year=2028,name='Admin report',assumptions='Declared fixture')
            s.add(plan);s.flush();pid=plan.id
            for f in frames_for((1,2)):
                days=month_days(f.data.year,f.data.month);admin=Employee(19,'Test Admin','ADMIN',False,(2,3))
                code=lambda d:'T' if d.weekday()<5 else 'OFF'
                data=replace(f.data,employees=(*f.data.employees,admin),previous={**f.data.previous,
                    **{(19,days[0]-timedelta(days=k)):code(days[0]-timedelta(days=k)) for k in range(1,7)}})
                cells={**f.baseline,**{(19,d):code(d) for d in days}}
                seed_month(s,data,cells,plan_id=pid)
        stats=annual_statistics(self.service,pid);keys=set(stats['employees'][0])
        self.assertTrue(all(set(e)==keys for e in stats['employees']))
        admin=next(e for e in stats['employees'] if e['employee_id']==19)
        self.assertIsNone(admin['target_shifts_lower']);self.assertGreater(admin['full_weekends'],0)
        exported=export_annual(self.service,pid,Path(self.tmp.name))
        self.assertGreater(exported.stat().st_size,1000)

    def test_full_weekend_crosses_month_and_monday_night(self):
        frames=frames_for((1,2),2027)
        frames=[replace(f,baseline={k:'OFF' for k in f.baseline}) for f in frames]
        frames[1].baseline[1,date(2027,2,1)]='1'
        context=YearMetrics(frames);codes=dict(context.original)
        event=next(j for j,e in enumerate(context.events) if e[0]==1 and e[2]==date(2027,1,31))
        self.assertEqual(context.full(event,codes),0)
        codes[1,date(2027,2,1)]='OFF';self.assertEqual(context.full(event,codes),1)
        self.assertTrue(any(p['sunday']=='2027-02-28' for p in context.pending))

    def test_weighted_targets_distinguish_ct_and_contract_credit(self):
        frames=frames_for((1,))
        f=frames[0];day=date(2028,1,1)
        data=replace(f.data,requests=(Request(1,day,'CT'),),employees=tuple(replace(e,target_credit=10) if e.id==2 else e for e in f.data.employees))
        codes=dict(f.baseline);codes[1,day]='CT'
        ctx=YearMetrics([replace(f,data=data,baseline=codes)])
        self.assertLess(ctx.weights['total_shifts'][1],ctx.weights['total_shifts'][3])
        self.assertLess(ctx.weights['total_shifts'][2],ctx.weights['total_shifts'][3])
        report=ctx.report(codes);row=next(r for r in report['employees'] if r['employee_id']==1)
        self.assertEqual(row['work'],row['total_shifts']+1)

    def test_incremental_ledger_and_cp_vector_match_independent_counts(self):
        frames=skewed_frames();ctx=YearMetrics(frames);options=RepairOptions(as_of=ASOF)
        search=ExchangeSearch(ctx,options)
        accepted=False
        for d in ctx.days:
            if search.try_exchange(14,13,[d]):accepted=True;break
        self.assertTrue(accepted)
        self.assertEqual(list(search.key),ctx.report(search.codes)['score'])
        local=[replace(frames[0],data=previous_with(frames[0].data,search.codes),baseline={k:search.codes[k] for k in frames[0].baseline})]
        right=replace(frames[1],baseline={k:search.codes[k] for k in frames[1].baseline})
        model=LinkedModel(local,right,options,quality=False,repair_terms=False)
        terms=add_terms(model,ctx,search.codes,[0])
        for key,var in model.values.items():model.model.add(var==CODE_ID[search.codes[key]])
        solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=5
        self.assertEqual(solver.solve(model.model),cp_model.OPTIMAL,solver.solution_info())
        self.assertEqual([int(solver.value(t)) for label,t in terms],ctx.report(search.codes)['score'])


class YearBalanceTests(DBCase):
    def test_cp_ties_never_replace_a_better_downstream_incumbent(self):
        frames=frames_for((1,));context=YearMetrics(frames);codes=dict(context.original)
        before=context.report(codes)
        proposed,attempts=refine_windows(context,codes,RepairOptions(as_of=ASOF,budget=7),
            deadline=perf_counter()+7,window_months=1,emit=lambda *a:None,cancelled=lambda:False)
        self.assertLessEqual(context.report(proposed)['score'],before['score'])
        self.assertTrue(attempts)
        validate_month(frames[0].data,proposed)

    def test_unfixed_cp_window_really_improves_a_valid_baseline(self):
        frames=[skewed_frames()[0]];context=YearMetrics(frames);codes=dict(context.original)
        before=context.report(codes)
        proposed,attempts=refine_windows(context,codes,RepairOptions(as_of=ASOF,budget=10),
            deadline=perf_counter()+10,window_months=1,emit=lambda *a:None,cancelled=lambda:False)
        self.assertLess(context.report(proposed)['score'],before['score'])
        self.assertTrue(any(layer['status'] in ('FEASIBLE','OPTIMAL') for attempt in attempts for layer in attempt['layers']))
        validate_month(frames[0].data,proposed)

    def test_real_search_improves_year_and_preserves_hard_rules(self):
        frames=skewed_frames();result=balance_year(frames,None,RepairOptions(as_of=ASOF,budget=7))
        self.assertEqual(result.status,'FEASIBLE');report=result.metrics['annual_balance']
        self.assertLess(report['after']['score'],report['before']['score'])
        self.assertLess(report['after']['ranges']['total_shifts']['gap'],report['before']['ranges']['total_shifts']['gap'])
        self.assertFalse(result.proof['annual_global_optimal'])
        self.assertGreaterEqual(sum(r['full_weekends'] for r in report['after']['employees']),sum(r['full_weekends'] for r in report['before']['employees']))
        self.assertTrue(all(b['errors']==0 for b in result.boundaries))
        for entry in result.entries:validate_month(entry['data'],entry['codes'])

    def test_actual_published_locks_scope_and_zero_change_cap(self):
        frames=skewed_frames()
        locked=next(iter(frames[2].baseline))
        frames=[replace(frames[0],status='ACTUAL'),replace(frames[1],status='PUBLISHED'),replace(frames[2],locks=(locked,))]
        result=balance_year(frames,None,RepairOptions(as_of=ASOF,budget=4,allowed_from=date(2028,3,1)))
        self.assertEqual(result.entries[0]['codes'],frames[0].baseline)
        self.assertEqual(result.entries[1]['codes'],frames[1].baseline)
        self.assertEqual(result.entries[2]['codes'][locked],frames[2].baseline[locked])
        zero=balance_year(frames,None,RepairOptions(as_of=ASOF,budget=2,max_changes=0))
        self.assertEqual(zero.metrics['changed_cells'],0)
        self.assertEqual(zero.metrics['annual_balance']['before']['score'],zero.metrics['annual_balance']['after']['score'])

    def test_cancelled_before_solving_has_no_candidate(self):
        result=balance_year(frames_for((1,)),None,RepairOptions(as_of=ASOF,budget=2),cancelled=lambda:True)
        self.assertEqual(result.status,'CANCELLED');self.assertFalse(result.entries)

    def seed_year(self):
        frames=skewed_frames();ids=[];original=[]
        with self.service.sessions.begin() as s:
            plan=m.AnnualPlan(year=2028,name='Balance test',assumptions='Declared fixture',baseline_frozen=True,status='PLANNED')
            s.add(plan);s.flush();pid=plan.id
            for f in frames:
                mid=seed_month(s,f.data,f.baseline,plan_id=pid);ids.append(mid);original.append(s.get(m.PlanningMonth,mid).current_version_id)
        return pid,ids,original

    def test_draft_atomic_api_confirm_keeps_v0_and_reports_year(self):
        pid,mids,original=self.seed_year()
        job=self.service.enqueue(JobInput(revision=1,mode='BALANCE',annual_plan_id=pid,budget=8,as_of=ASOF))
        execute(self.engine,job['id']);j=self.service.job(job['id'])
        self.assertEqual(j['status'],'SUCCEEDED',j['error'] or j['log'])
        self.assertEqual(len(j['result']['version_ids']),3)
        self.assertIn('annual_balance',j['result'])
        with self.service.sessions() as s:self.assertEqual([s.get(m.PlanningMonth,mid).current_version_id for mid in mids],original)
        payload=ConfirmInput(revision=1,expected_current_version_id=original[0])
        with self.assertRaises(Conflict):self.service.confirm(j['result']['version_ids'][0],payload)
        with TestClient(create_app(self.path,start_workers=False)) as client:
            response=client.post('/api/jobs/'+j['id']+'/confirm',json=payload.model_dump(mode='json'))
            self.assertEqual(response.status_code,200,response.text)
        stats=annual_statistics(self.service,pid)
        self.assertIsNotNone(stats['balance_summary'])
        self.assertTrue(all('total_shifts' in e for e in stats['employees']))
        with self.service.sessions() as s:
            self.assertEqual([s.get(m.PlanningMonth,mid).baseline_version_id for mid in mids],original)
            self.assertEqual([s.get(m.PlanningMonth,mid).current_version_id for mid in mids],j['result']['version_ids'])
            self.assertEqual(revision(s),2)

    def test_stale_group_confirmation_rolls_back_every_month(self):
        pid,mids,original=self.seed_year()
        job=self.service.enqueue(JobInput(revision=1,mode='BALANCE',annual_plan_id=pid,budget=2,max_changes=0,as_of=ASOF))
        execute(self.engine,job['id']);self.assertEqual(self.service.job(job['id'])['status'],'SUCCEEDED')
        updated=self.service.lock(LockInput(revision=1,month_id=mids[0],employee_ids=[1],day=date(2028,1,1)))
        with self.assertRaises(Conflict):confirm_cascade(self.service,job['id'],ConfirmInput(revision=updated['revision'],expected_current_version_id=original[0]))
        with self.service.sessions() as s:self.assertEqual([s.get(m.PlanningMonth,mid).current_version_id for mid in mids],original)

    def test_atomic_balance_does_not_downgrade_published_first_month(self):
        pid,mids,original=self.seed_year()
        with self.service.sessions.begin() as s:
            data,prefs=load_month(s,mids[0]);codes=codes_for(s,original[0]);report=validate_month(data,codes)
            self.assertFalse(report.hard_pending)
            published=save_version(s,mids[0],snapshot(s,mids[0]),codes,report.to_dict(),source='TEST_PUBLISHED',
                parent=original[0],status='PUBLISHED')
            published_id=published.id;s.get(m.PlanningMonth,mids[0]).current_version_id=published_id
        job=self.service.enqueue(JobInput(revision=1,mode='BALANCE',annual_plan_id=pid,budget=2,max_changes=0,as_of=ASOF))
        execute(self.engine,job['id']);result=self.service.job(job['id'])
        self.assertEqual(result['status'],'SUCCEEDED',result['error'])
        confirm_cascade(self.service,job['id'],ConfirmInput(revision=1,expected_current_version_id=published_id))
        with self.service.sessions() as s:
            first=s.get(m.ScheduleVersion,s.get(m.PlanningMonth,mids[0]).current_version_id)
            self.assertEqual(first.status,'PUBLISHED')
            self.assertEqual(codes_for(s,first.id),codes_for(s,published_id))
            self.assertEqual(s.get(m.PlanningMonth,mids[0]).baseline_version_id,original[0])
