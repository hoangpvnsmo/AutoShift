"""Year reset preserves inputs/history and permits a real fresh annual solve."""
from test_v50 import DBCase,fixture,seed_month,ASOF
from contextlib import closing
from dataclasses import replace
from datetime import date,datetime
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
import sqlite3
from sqlalchemy import select
from fastapi.testclient import TestClient
from autoshift5 import models as m
from autoshift5.db import revision
from autoshift5.domain import Conflict,LOCAL_TZ
from autoshift5.schemas import YearClearInput,JobInput
from autoshift5.year_clear import preview_clear,clear_year
from autoshift5.repository import snapshot,save_version
from autoshift5.validation import validate_month,boundary_report
from autoshift5.worker import execute
from autoshift5.annual import confirm_plan
from autoshift5.api import create_app


class ClearYearTests(DBCase):
    def setUp(self):
        super().setUp()
        for target,value in [('autoshift5.year_clear.ROOT',Path(self.tmp.name)),('autoshift5.year_clear.now',lambda:ASOF)]:
            patcher=patch(target,value);patcher.start();self.addCleanup(patcher.stop)

    def seed_year(self,numbers=(1,2),year=2028):
        with self.service.sessions.begin() as s:
            p=m.AnnualPlan(name=f'Test {year}',year=year,status='PLANNED',baseline_frozen=True)
            s.add(p);s.flush();pid=p.id
            mids=[seed_month(s,*fixture(year,n,fixed=True),plan_id=pid) for n in numbers]
        return pid,mids

    def clear(self,pid,rev=1,year=2028):
        return clear_year(self.service,pid,YearClearInput(revision=rev,year=year))

    def rows(self,table):
        with self.engine.connect() as c:return c.exec_driver_sql(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()

    def test_clear_preserves_inputs_immutable_history_neighbors_and_wal_backup(self):
        pid,mids=self.seed_year()
        with self.service.sessions.begin() as s:
            prev=seed_month(s,*fixture(2027,12));nxt=seed_month(s,*fixture(2029,1))
            s.add_all([m.ScheduleLock(month_id=mid,employee_id=1,day=date(2028,n,1),reason='old lock') for n,mid in enumerate(mids,1)])
            s.add(m.ScheduleLock(month_id=prev,employee_id=1,day=date(2027,12,31),reason='keep'))
            s.add(m.Preference(employee_id=1,day=date(2028,1,2),kind='PREFER_OFF',weight=2))
            s.add(m.Holiday(month_id=mids[0],day=date(2028,1,1),note='keep'))
        preserved=['employees','employee_monthly_settings','employee_permissions','rule_profiles','holidays','staffing_demands','preferences',
            'schedule_versions','schedule_assignments','input_snapshots','objective_scores','validation_results','schedule_changes']
        before={t:self.rows(t) for t in preserved}
        requests=self.rows('requests');neighbors={i:self.service.calendar(i)['month'] for i in (prev,nxt)}
        preview=preview_clear(self.service,pid)
        self.assertEqual((preview['current_months'],preview['locks_to_clear']),(2,2))
        result=self.clear(pid)
        self.assertEqual(result['revision'],2)
        self.assertTrue(Path(result['backup_path']).is_file())
        with closing(sqlite3.connect(result['backup_path'])) as c:
            self.assertEqual(c.execute('pragma integrity_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('select revision from input_state').fetchone()[0],1)
            self.assertEqual(c.execute('select count(*) from planning_months where year=2028 and current_version_id is not null').fetchone()[0],2)
        for t in preserved:self.assertEqual(before[t],self.rows(t),t)
        with self.service.sessions() as s:
            self.assertFalse(s.get(m.AnnualPlan,pid).baseline_frozen)
            self.assertEqual(s.get(m.AnnualPlan,pid).status,'DRAFT')
            for mid in mids:
                mo=s.get(m.PlanningMonth,mid);self.assertIsNone(mo.current_version_id);self.assertIsNone(mo.baseline_version_id)
            hist=list(s.scalars(select(m.HistoricalAssignment).where(m.HistoricalAssignment.month_id.in_(mids))))
            self.assertTrue(hist);self.assertTrue(all(h.day.year==2027 for h in hist))
            self.assertEqual([l.month_id for l in s.scalars(select(m.ScheduleLock))],[prev])
            self.assertEqual(len(list(s.scalars(select(m.RequestRevision)))),len(requests))
        for old,new in zip(requests,self.rows('requests')):
            self.assertEqual(old[:5],new[:5]);self.assertEqual(new[5],'APPROVED');self.assertEqual(new[6],old[6]+1)
        for mid in mids:
            self.assertEqual(self.service.calendar(mid)['assignments'],[])
        for mid in (prev,nxt):self.assertEqual(self.service.calendar(mid)['month'],neighbors[mid])
        with self.engine.connect() as c:self.assertEqual(c.exec_driver_sql('pragma foreign_key_check').fetchall(),[])

    def test_clear_is_idempotent_without_second_backup_or_revision_bump(self):
        pid,_=self.seed_year();first=self.clear(pid);second=self.clear(pid,2)
        self.assertFalse(second['cleared']);self.assertEqual(second['revision'],2)
        self.assertEqual(second['backup_path'],first['backup_path'])
        self.assertEqual(len(list(Path(self.tmp.name).glob('artifacts/backups/*.db'))),1)
        self.assertTrue(preview_clear(self.service,pid)['empty'])

    def test_wrong_year_and_stale_preview_are_atomic(self):
        pid,_=self.seed_year();before=self.rows('planning_months')
        with self.assertRaises(ValueError):self.clear(pid,year=2029)
        with self.assertRaises(Conflict):self.clear(pid,rev=2)
        self.assertEqual(before,self.rows('planning_months'))
        self.assertFalse(list(Path(self.tmp.name).glob('artifacts/backups/*.db')))

    def test_active_job_anywhere_blocks_clear(self):
        pid,_=self.seed_year()
        with self.service.sessions.begin() as s:mid=seed_month(s,*fixture(2027,12))
        self.service.enqueue(JobInput(revision=1,mode='FRESH',month_id=mid,budget=1))
        self.assertFalse(preview_clear(self.service,pid)['can_clear'])
        with self.assertRaises(Conflict):self.clear(pid)

    def test_published_and_started_current_schedules_are_protected(self):
        for status in ('PUBLISHED','ACTUAL'):
            with self.subTest(status=status):
                # Guards intentionally prevent changing a confirmed row; seed through DRAFT.
                with self.service.sessions.begin() as s:
                    p=m.AnnualPlan(name=status,year=2028);s.add(p);s.flush()
                    data,codes=fixture(2028,1 if status=='PUBLISHED' else 2)
                    mid=seed_month(s,data,plan_id=p.id)
                    snap=snapshot(s,mid);v=save_version(s,mid,snap,codes,validate_month(data,codes).to_dict(),source='TEST')
                    v.status=status;s.get(m.PlanningMonth,mid).current_version_id=v.id;pid=p.id
                with self.assertRaises(Conflict):self.clear(pid)
        # The separate past-day check also protects future-date PLANNED versions once started.
        with self.service.sessions.begin() as s:pid=s.scalar(select(m.AnnualPlan.id).limit(1))
        preview=preview_clear(self.service,pid)
        self.assertTrue(preview['blocked_reasons'])

    def test_planned_schedule_blocks_once_first_night_has_started(self):
        pid,_=self.seed_year(numbers=(1,))
        with patch('autoshift5.year_clear.now',lambda:datetime(2027,12,31,23,tzinfo=LOCAL_TZ)):
            self.assertFalse(preview_clear(self.service,pid)['can_clear'])
            with self.assertRaises(Conflict):self.clear(pid)

    def test_completed_jobs_archive_by_actual_results_not_snapshot_only(self):
        pid,mids=self.seed_year(numbers=(1,))
        with self.service.sessions.begin() as s:
            prev=seed_month(s,*fixture(2027,12));pv=s.get(m.PlanningMonth,prev).current_version_id
            tv=s.get(m.PlanningMonth,mids[0]).current_version_id;sid=s.get(m.ScheduleVersion,tv).snapshot_id
            def job(mid,plan,versions,confirmed):
                j=m.SolverRun(id=str(uuid4()),scope_key=str(uuid4()),month_id=mid,annual_plan_id=plan,mode='ANNUAL' if plan else 'FRESH',
                    revision=1,status='SUCCEEDED',options={},snapshots=[sid],result={'version_ids':versions,'confirmed':confirmed})
                s.add(j);return j.id
            own=job(None,pid,[tv],True);coupled=job(prev,None,[pv,tv],False);foreign=job(prev,None,[pv],True)
        self.clear(pid)
        self.assertEqual(self.service.job(own)['status'],'ARCHIVED')
        self.assertEqual(self.service.job(coupled)['status'],'ARCHIVED')
        self.assertEqual(self.service.job(foreign)['status'],'SUCCEEDED')

    def test_unapproved_draft_cannot_be_confirmed_after_clear(self):
        pid,mids=self.seed_year(numbers=(1,))
        with self.service.sessions.begin() as s:
            data,codes=fixture();snap=snapshot(s,mids[0])
            v=save_version(s,mids[0],snap,codes,validate_month(data,codes).to_dict(),source='ANNUAL');vid=v.id
        self.clear(pid)
        with self.assertRaises(Conflict):confirm_plan(self.service,pid,[vid],2)
        self.assertIsNone(self.service.calendar(mids[0])['month']['current_version_id'])

    def test_real_annual_after_clear_uses_new_inputs_and_confirms_new_baseline(self):
        pid,mids=self.seed_year();old=[self.service.calendar(mid)['month']['current_version_id'] for mid in mids]
        self.clear(pid)
        job=self.service.enqueue(JobInput(revision=2,mode='ANNUAL',annual_plan_id=pid,budget=20))
        execute(self.engine,job['id']);result=self.service.job(job['id'])
        self.assertEqual(result['status'],'SUCCEEDED',result['error'])
        new=result['result']['version_ids'];self.assertEqual(len(new),2);self.assertFalse(set(new)&set(old))
        with self.service.sessions() as s:
            for vid in new:
                v=s.get(m.ScheduleVersion,vid);snap=s.get(m.InputSnapshot,v.snapshot_id)
                self.assertIsNone(v.parent_version_id);self.assertIsNone(snap.payload['_context']['current_version'])
        confirmed=confirm_plan(self.service,pid,new,2)
        self.assertEqual(confirmed['revision'],3)
        with self.service.sessions() as s:
            self.assertEqual([s.get(m.PlanningMonth,mid).baseline_version_id for mid in mids],new)
            self.assertTrue(all(r.status=='APPLIED' for r in s.scalars(select(m.Request))))

    def test_partial_year_keeps_initial_anchor_and_checks_following_year_real_solver(self):
        pid,mids=self.seed_year(numbers=(12,))
        with self.service.sessions.begin() as s:seed_month(s,*fixture(2029,1))
        self.clear(pid)
        with self.service.sessions() as s:
            self.assertEqual(len(list(s.scalars(select(m.HistoricalAssignment).where(m.HistoricalAssignment.month_id==mids[0])))),108)
        job=self.service.enqueue(JobInput(revision=2,mode='ANNUAL',annual_plan_id=pid,budget=15,annual_balance=False))
        execute(self.engine,job['id']);result=self.service.job(job['id'])
        self.assertEqual(result['status'],'SUCCEEDED',result['error'])

    def test_rebuilt_year_conflict_with_following_year_does_not_save_partial_results(self):
        pid,mids=self.seed_year(numbers=(12,))
        left,lc=fixture(2028,12);right,rc=fixture(2029,1)
        # An independently valid shifted rotation has a different initial anchor.
        shift={(i,d):('OFF' if c!='OFF' else str((i-1)//6+1)) for (i,d),c in rc.items()}
        history={(i,d):('OFF' if c!='OFF' else str((i-1)//6+1)) for (i,d),c in right.previous.items()}
        right=replace(right,previous=history)
        self.assertFalse(validate_month(right,shift).errors)
        self.assertTrue(boundary_report(left,lc,right,shift)['errors'])
        with self.service.sessions.begin() as s:seed_month(s,right,shift)
        self.clear(pid)
        before=self.rows('schedule_versions')
        job=self.service.enqueue(JobInput(revision=2,mode='ANNUAL',annual_plan_id=pid,budget=15,annual_balance=False))
        execute(self.engine,job['id']);result=self.service.job(job['id'])
        self.assertEqual(result['status'],'BOUNDARY_CONFLICT',result['error'])
        self.assertEqual(before,self.rows('schedule_versions'))
        self.assertIsNone(self.service.calendar(mids[0])['month']['current_version_id'])

    def test_api_preview_and_clear_contract(self):
        pid,_=self.seed_year()
        with TestClient(create_app(self.path,start_workers=False)) as client:
            preview=client.get(f'/api/annual-plans/{pid}/clear-preview').json()
            self.assertEqual(preview['year'],2028)
            self.assertEqual(client.post(f'/api/annual-plans/{pid}/clear',json={'year':2029,'revision':1}).status_code,422)
            self.assertEqual(client.post(f'/api/annual-plans/{pid}/clear',json={'year':2028,'revision':2}).status_code,409)
            r=client.post(f'/api/annual-plans/{pid}/clear',json={'year':2028,'revision':1})
            self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['cleared'])

    def test_backup_failure_leaves_year_and_revision_unchanged(self):
        pid,_=self.seed_year();before=self.rows('planning_months')
        with patch('autoshift5.year_clear.backup',side_effect=OSError('backup unavailable')):
            with self.assertRaises(OSError):self.clear(pid)
        self.assertEqual(before,self.rows('planning_months'))
        with self.service.sessions() as s:self.assertEqual(revision(s),1)

    def test_request_contents_and_non_applied_statuses_survive_clear(self):
        pid,mids=self.seed_year(numbers=(1,))
        with self.service.sessions.begin() as s:
            # Cancelled/rejected records coexist with the active fixed requests.
            for status in ('CANCELLED','REJECTED'):
                s.add(m.Request(id=str(uuid4()),employee_id=1,day=date(2028,1,1),code='CT',reason='Keep reason',status=status))
            # DRAFT/SUBMITTED/APPROVED are inputs too; clearing never approves them implicitly.
            active=list(s.scalars(select(m.Request).order_by(m.Request.id).limit(3)))
            for r,status in zip(active,('DRAFT','SUBMITTED','APPROVED')):r.status=status
        before=self.rows('requests');self.clear(pid);after=self.rows('requests')
        for a,b in zip(before,after):
            self.assertEqual(a[:5],b[:5])
            self.assertEqual(b[5],'APPROVED' if a[5]=='APPLIED' else a[5])
            self.assertEqual(b[6],a[6]+int(a[5]=='APPLIED'))
