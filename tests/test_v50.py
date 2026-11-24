"""5.0 regression and real CP-SAT integration; no mock Solver results."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import json
import unittest
from sqlalchemy import select, func, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from core.data.models import MonthData, Employee, Request
from core.data.data_loader import load_data
from core.data.calendar_utils import month_days
from core.objectives.preference_checks import load_preferences
from core.validation.credit_checks import demand_requirements
from validator.quality import score_vector
from validator.excel_schedule import read_schedule
from autoshift5 import models as m
from autoshift5.db import make_engine, initialize, ROOT, backup, revision, bump
from autoshift5.domain import serialize, deserialize, changed_cells, history_gaps, LOCAL_TZ, Conflict, Unsupported
from autoshift5.repository import load_month, snapshot, save_version, codes_for
from autoshift5.importer import import_legacy
from autoshift5.repair import RepairOptions, repair_schedule
from autoshift5.validation import validate_repair, validate_month, boundary_report, repair_metrics
from autoshift5.services import Service
from autoshift5.schemas import RequestBatch, RequestTransition, JobInput, ConfirmInput, LockInput, DraftEdit, CellEdit
from autoshift5.worker import execute
from autoshift5.annual import confirm_plan, annual_statistics
from autoshift5.reporting import export_version
from autoshift5.api import create_app

ORIGIN=date(2028,1,1)
ASOF=datetime(2027,12,1,tzinfo=LOCAL_TZ)
INPUT=ROOT/'input/input.xlsx'
RULES=ROOT/'config/rules.json'
SCHEDULE=ROOT/'output/AutoShift_2026-12_4_9_20261125_021312_782403.xlsx'


def fixture(year=2028, month=1, *, fixed=False):
    """Declared synthetic fixture: 18 leaders, one shift type each, 3 on / 3 off.
    Night maximum 3 is test-only; the real user's configuration remains 2.
    """
    rules=json.loads(RULES.read_text(encoding='utf-8'))
    rules['max_consecutive_night_shifts']=3
    rules['optimization']={k:False for k in rules['optimization']}
    people=tuple(Employee(i,f'Test {i}','SHIFT',True,((i-1)//6+1,)) for i in range(1,19))
    days=month_days(year,month)
    def code(i,d):
        return str((i-1)//6+1) if ((d-ORIGIN).days+(i-1)%6)%6<3 else 'OFF'
    codes={(e.id,d):code(e.id,d) for e in people for d in days}
    previous={(e.id,days[0]-timedelta(days=k)):code(e.id,days[0]-timedelta(days=k)) for e in people for k in range(1,7)}
    requests=tuple(Request(i,d,c) for (i,d),c in codes.items()) if fixed else ()
    return MonthData(year,month,frozenset(),people,requests,previous,rules),codes


def seed_month(s,data,codes=None,*,plan_id=None):
    profile=m.RuleProfile(name=str(uuid4()),revision=1,rules=data.rules)
    s.add(profile);s.flush()
    month=m.PlanningMonth(year=data.year,month=data.month,rule_profile_id=profile.id,annual_plan_id=plan_id,
                         assumptions='Synthetic fixture, 3-on/3-off, explicit six-day history; no holidays.',input_confirmed=True)
    s.add(month);s.flush()
    days=month_days(data.year,data.month)
    for pos,e in enumerate(data.employees):
        if s.get(m.Employee,e.id) is None:
            s.add(m.Employee(id=e.id,name=e.name,group=e.group,is_leader=e.is_leader));s.flush()
        s.add(m.MonthlySetting(month_id=month.id,employee_id=e.id,position=pos,name=e.name,group=e.group,is_leader=e.is_leader,target_credit=e.target_credit))
        s.add_all(m.Permission(employee_id=e.id,shift=k,valid_from=days[0],valid_until=days[-1]) for k in e.allowed_shifts)
    s.add_all(m.HistoricalAssignment(month_id=month.id,employee_id=i,day=d,code=c) for (i,d),c in data.previous.items())
    s.add_all(m.Demand(month_id=month.id,day=d,shift=k,count=n) for (d,k),n in demand_requirements(data).items())
    for req in data.requests:
        s.add(m.Request(id=str(uuid4()),employee_id=req.employee_id,day=req.day,code=req.code,reason=req.note,status='APPLIED' if codes else 'APPROVED',revision=1))
    s.flush()
    if codes:
        snap=snapshot(s,month.id)
        report=validate_month(data,codes)
        v=save_version(s,month.id,snap,codes,report.to_dict(),source='TEST_BASELINE',status='PLANNED',scores=score_vector(report))
        month.current_version_id,month.baseline_version_id=v.id,v.id
    s.flush()
    return month.id


class DBCase(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'test.db';self.engine=make_engine(self.path);initialize(self.engine)
        self.addCleanup(self.engine.dispose);self.service=Service(self.engine)

    def seed(self, fixed=False):
        data,codes=fixture(fixed=fixed)
        with self.service.sessions.begin() as s: mid=seed_month(s,data,codes)
        return data,codes,mid

class DatabaseTests(DBCase):
    def test_sqlite_settings_and_foreign_keys(self):
        with self.engine.connect() as c:
            self.assertEqual(c.exec_driver_sql('PRAGMA foreign_keys').scalar(),1)
            self.assertEqual(c.exec_driver_sql('PRAGMA journal_mode').scalar(),'wal')
        with self.assertRaises(IntegrityError),self.service.sessions.begin() as s:
            s.add(m.Assignment(version_id='unknown',employee_id=123,day=ORIGIN,code='OFF'))

    def test_dry_run_and_equivalence(self):
        r=import_legacy(self.engine,INPUT,RULES,SCHEDULE,dry_run=True)
        self.assertTrue(all(r['field_equivalence'].values()))
        with self.service.sessions() as s:self.assertEqual(s.scalar(select(func.count()).select_from(m.Employee)),0)

    def test_import_idempotent(self):
        a=import_legacy(self.engine,INPUT,RULES,SCHEDULE,dry_run=False)
        b=import_legacy(self.engine,INPUT,RULES,SCHEDULE,dry_run=False)
        self.assertTrue(b['already_imported']);self.assertEqual(a['version_id'],b['version_id'])
        with self.service.sessions() as s:self.assertEqual(s.scalar(select(func.count()).select_from(m.Assignment)),651)

    def test_import_unknown_sheet_rolls_back(self):
        from openpyxl import load_workbook
        p=Path(self.tmp.name)/'unknown.xlsx';w=load_workbook(INPUT);w.create_sheet('Unknown').append(['unmapped']);w.save(p);w.close()
        with self.assertRaisesRegex(ValueError,'chưa nhận diện'):import_legacy(self.engine,p,RULES,dry_run=False)
        with self.service.sessions() as s:self.assertEqual(revision(s),1)

    def test_import_conflicting_request_rejected(self):
        from openpyxl import load_workbook
        p=Path(self.tmp.name)/'conflict.xlsx';w=load_workbook(INPUT);row=list(w['Requests'].values)[1];w['Requests'].append(row);w.save(p);w.close()
        with self.assertRaises(ValueError):import_legacy(self.engine,p,RULES,dry_run=False)
        with self.service.sessions() as s:self.assertEqual(s.scalar(select(func.count()).select_from(m.Employee)),0)

    def test_source_files_unchanged(self):
        from hashlib import sha256
        paths=[INPUT,RULES,SCHEDULE,ROOT/'output/checkpoints/2026-12_real.json']
        old={p:sha256(p.read_bytes()).hexdigest() for p in paths}
        import_legacy(self.engine,INPUT,RULES,SCHEDULE,dry_run=False)
        self.assertEqual(old,{p:sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_full_codes_are_immutable_at_database_level(self):
        _,_,mid=self.seed();v=self.service.calendar(mid)['version']['id']
        with self.assertRaises(IntegrityError),self.service.sessions.begin() as s:
            s.execute(update(m.Assignment).where(m.Assignment.version_id==v).values(code='OFF'))
        with self.assertRaises(IntegrityError),self.service.sessions.begin() as s:
            s.execute(update(m.ScheduleVersion).where(m.ScheduleVersion.id==v).values(source='CHANGED'))

    def test_snapshot_roundtrip(self):
        data,_=fixture();raw=serialize(data);restored,prefs=deserialize(raw)
        self.assertEqual(data,restored);self.assertFalse(prefs)

    def test_request_overlay_preserves_schedule(self):
        _,codes,mid=self.seed();v=self.service.calendar(mid)['version']['id']
        r=self.service.request_batch(RequestBatch(revision=1,employee_ids=[1],from_date=ORIGIN,to_date=ORIGIN,code='DT'))
        cal=self.service.calendar(mid)
        self.assertEqual(cal['version']['id'],v)
        self.assertEqual(next(a['code'] for a in cal['assignments'] if a['employee_id']==1 and a['day']==str(ORIGIN)),'1')
        self.assertEqual(cal['requests'][0]['status'],'APPROVED')
        with self.service.sessions() as s:self.assertEqual(codes_for(s,v),codes)

    def test_conflict_and_explicit_replacement(self):
        self.seed();p=dict(employee_ids=[1],from_date=ORIGIN,to_date=ORIGIN,code='OFF')
        first=self.service.request_batch(RequestBatch(revision=1,**p))
        with self.assertRaises(Conflict):self.service.request_batch(RequestBatch(revision=2,**p))
        second=self.service.request_batch(RequestBatch(revision=2,replace_ids=[first['requests'][0]['id']],**{**p,'code':'DT'}))
        rows=self.service.requests();self.assertEqual({r['status'] for r in rows},{'CANCELLED','APPROVED'})
        self.assertEqual(second['revision'],3)

    def test_request_cancellation_revision(self):
        self.seed();r=self.service.request_batch(RequestBatch(revision=1,employee_ids=[1],from_date=ORIGIN,to_date=ORIGIN,code='DT'))['requests'][0]
        self.service.request_transition(r['id'],RequestTransition(revision=2,request_revision=1,status='CANCELLED'))
        self.assertEqual(self.service.requests()[0]['status'],'CANCELLED')
        with self.assertRaises(Conflict):self.service.request_transition(r['id'],RequestTransition(revision=3,request_revision=1,status='APPROVED'))

    def test_bulk_cross_month_and_rollback(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.service.request_batch(RequestBatch(revision=1,employee_ids=[1,2],from_date=date(2028,1,31),to_date=date(2028,2,1),code='DT'))
        self.assertEqual(self.service.requests(),[]);self.assertEqual(self.service.state()['revision'],1)

    def test_optimistic_lock(self):
        self.seed()
        with self.service.sessions.begin() as s:bump(s,1)
        with self.assertRaises(Conflict):self.service.lock(LockInput(revision=1,month_id=1,employee_ids=[1],day=ORIGIN))

    def test_backup_has_wal_data(self):
        self.seed();destination=Path(self.tmp.name)/'backup.db';backup(self.path,destination)
        e=make_engine(destination)
        with sessionmaker(e)() as s:self.assertEqual(s.scalar(select(func.count()).select_from(m.Assignment)),558)
        e.dispose()
        with self.assertRaises(FileExistsError):backup(self.path,destination)

    def test_jobs_do_not_conflict(self):
        _,_,mid=self.seed()
        self.service.enqueue(JobInput(revision=1,mode='FRESH',month_id=mid,budget=1))
        with self.assertRaises(Conflict):self.service.enqueue(JobInput(revision=1,mode='FRESH',month_id=mid,budget=1))

    def test_stale_job_rejected_before_persistence(self):
        _,_,mid=self.seed(fixed=True)
        j=self.service.enqueue(JobInput(revision=1,mode='FRESH',month_id=mid,budget=5))
        with self.service.sessions.begin() as s:bump(s,1)
        execute(self.engine,j['id'])
        result=self.service.job(j['id']);self.assertEqual(result['status'],'STALE',result['error'])
        with self.service.sessions() as s:self.assertEqual(s.scalar(select(func.count()).select_from(m.ScheduleVersion)),1)

    def test_cancelled_job_no_version(self):
        _,_,mid=self.seed()
        j=self.service.enqueue(JobInput(revision=1,mode='FRESH',month_id=mid,budget=2))
        with self.service.sessions.begin() as s:s.get(m.SolverRun,j['id']).cancel_requested=True
        execute(self.engine,j['id']);self.assertEqual(self.service.job(j['id'])['status'],'CANCELLED')

    def test_db_fresh_and_resume_real_solver(self):
        _,_,mid=self.seed(fixed=True)
        j=self.service.enqueue(JobInput(revision=1,mode='FRESH',month_id=mid,budget=8));execute(self.engine,j['id'])
        first=self.service.job(j['id']);self.assertEqual(first['status'],'SUCCEEDED',first['error'])
        vid=first['result']['version_ids'][0]
        j=self.service.enqueue(JobInput(revision=1,mode='RESUME',month_id=mid,baseline_version_id=vid,budget=8,passes=3));execute(self.engine,j['id'])
        last=self.service.job(j['id']);self.assertEqual(last['status'],'SUCCEEDED',last['error'])
        with self.service.sessions() as s:
            after=s.get(m.ScheduleVersion,last['result']['version_ids'][0]);self.assertEqual(after.proof['reused_prefix'],9)

    def test_repair_review_confirm_and_export_real_solver(self):
        data,codes,mid=self.seed();old=self.service.calendar(mid)['version']['id']
        req=self.service.request_batch(RequestBatch(revision=1,employee_ids=[1],from_date=ORIGIN,to_date=ORIGIN,code='DT'))['requests'][0]
        j=self.service.enqueue(JobInput(revision=2,mode='REPAIR',month_id=mid,baseline_version_id=old,request_ids=[req['id']],as_of=ASOF,budget=10))
        execute(self.engine,j['id']);job=self.service.job(j['id']);self.assertEqual(job['status'],'SUCCEEDED',job['error'])
        vid=job['result']['version_ids'][0]
        self.assertEqual(job['result']['repair']['changed_cells'],2)
        self.assertEqual(self.service.calendar(mid)['version']['id'],old)
        self.service.confirm(vid,ConfirmInput(revision=2,expected_current_version_id=old,status='PLANNED'))
        self.assertEqual(self.service.requests()[0]['status'],'APPLIED')
        path=export_version(self.service,vid,self.tmp.name)
        with self.service.sessions() as s:
            new_data,_=deserialize(s.get(m.InputSnapshot,s.get(m.ScheduleVersion,vid).snapshot_id).payload)
            self.assertEqual(dict(((i,d),c) for i,d,c in read_schedule(path,new_data)),codes_for(s,vid))
            self.assertEqual(codes_for(s,old),codes)
        from openpyxl import load_workbook
        w=load_workbook(path);self.assertEqual(w.sheetnames,['Lich truc','Thong ke','Bo tri ca','Kiem tra','Schedule','Huong dan','Thay doi']);w.close()

    def test_confirm_stale_pointer_rejected(self):
        _,_,mid=self.seed(fixed=True)
        j=self.service.enqueue(JobInput(revision=1,mode='FRESH',month_id=mid,budget=5));execute(self.engine,j['id'])
        vid=self.service.job(j['id'])['result']['version_ids'][0]
        with self.assertRaises(Conflict):self.service.confirm(vid,ConfirmInput(revision=1,expected_current_version_id=None))


class RepairSolverTests(unittest.TestCase):
    def test_admin_t_to_shift_and_no_admin_increase(self):
        data,codes=fixture()
        admin=Employee(19,'Test ADMIN','ADMIN',False,(2,3))
        days=month_days(data.year,data.month)
        admin_code=lambda d:'T' if d.weekday()<5 else 'OFF'
        baseline={**codes,**{(19,d):admin_code(d) for d in days}}
        previous={**data.previous,**{(19,days[0]-timedelta(days=k)):admin_code(days[0]-timedelta(days=k)) for k in range(1,7)}}
        day=date(2028,1,3)
        data=replace(data,employees=(*data.employees,admin),previous=previous,requests=(Request(19,day,'2'),))
        options=RepairOptions(as_of=ASOF,budget=6)
        result=repair_schedule(data,baseline,options=options,direct_cells=[(19,day)])
        self.assertEqual(result.codes[19,day],'2')
        self.assertEqual(result.metrics['changed_cells'],2)
        self.assertEqual(result.metrics['admin_after']-result.metrics['admin_before'],1)
        blocked=repair_schedule(data,baseline,options=replace(options,no_admin_increase=True),direct_cells=[(19,day)])
        self.assertEqual(blocked.status,'INFEASIBLE')

    def test_two_people_swap_real_solver(self):
        data,codes=fixture()
        data=replace(data,employees=tuple(replace(e,allowed_shifts=(1,2,3)) for e in data.employees),
                     requests=(Request(1,ORIGIN,'2'),Request(7,ORIGIN,'1')))
        result=repair_schedule(data,codes,options=RepairOptions(as_of=ASOF,budget=6),direct_cells=[(1,ORIGIN),(7,ORIGIN)])
        self.assertIsNotNone(result.codes,result.status)
        self.assertEqual(result.metrics['changed_cells'],2)
        self.assertEqual(result.metrics['other_people'],0)
        self.assertEqual((result.codes[1,ORIGIN],result.codes[7,ORIGIN]),('2','1'))

    def test_no_new_request_no_changes(self):
        data,codes=fixture();r=repair_schedule(data,codes,options=RepairOptions(as_of=ASOF,budget=2))
        self.assertEqual(r.codes,codes);self.assertEqual(r.metrics['unchanged_ratio'],1);self.assertTrue(r.proof['r1_global_optimal'])

    def test_special_codes_and_one_cell_accounting(self):
        data,codes=fixture();off=next(k for k,c in codes.items() if c=='OFF')
        for code in ('OFF','DT','CT','BKD'):
            with self.subTest(code=code):
                new=replace(data,requests=(Request(*off,code),))
                r=repair_schedule(new,codes,options=RepairOptions(as_of=ASOF,budget=4),direct_cells=[off])
                self.assertIsNotNone(r.codes,r.status)
                self.assertEqual(r.codes[off],code)
                self.assertEqual(r.metrics['changed_cells'],int(code!='OFF'))

    def test_shift_to_shift_counts_one_cell(self):
        a={(1,ORIGIN):'1'};b={(1,ORIGIN):'2'}
        self.assertEqual(len(changed_cells(a,b)),1)
        self.assertEqual(len(changed_cells({(1,ORIGIN):'OFF'},{(1,ORIGIN):'DT'})),1)
        self.assertEqual(len(changed_cells({(1,ORIGIN):'T'},{(1,ORIGIN):'2'})),1)

    def test_small_r1_optimum_and_cell_limit(self):
        data,codes=fixture();new=replace(data,requests=(Request(1,ORIGIN,'OFF'),))
        opts=RepairOptions(as_of=ASOF,budget=8,mode='OPTIMAL',max_changes=2)
        r=repair_schedule(new,codes,options=opts,direct_cells=[(1,ORIGIN)])
        # One forced removal + exact staffing implies at least two changed cells.
        self.assertEqual(r.metrics['changed_cells'],2)
        self.assertTrue(r.proof['r1_global_optimal'])
        impossible=repair_schedule(new,codes,options=replace(opts,max_changes=1),direct_cells=[(1,ORIGIN)])
        self.assertEqual(impossible.status,'INFEASIBLE')

    def test_locks_past_and_night_started(self):
        data,codes=fixture();new=replace(data,requests=(Request(1,ORIGIN,'DT'),))
        for opts,locks in [(RepairOptions(as_of=ASOF,budget=3),[(1,ORIGIN)]),
                           (RepairOptions(as_of=datetime(2027,12,31,22,0,tzinfo=LOCAL_TZ),budget=3),[])]:
            r=repair_schedule(new,codes,options=opts,direct_cells=[(1,ORIGIN)],locks=locks)
            self.assertIsNone(r.codes);self.assertEqual(r.status,'INFEASIBLE')

    def test_outside_scope_rejected(self):
        data,codes=fixture();new=replace(data,requests=(Request(1,ORIGIN,'DT'),))
        r=repair_schedule(new,codes,options=RepairOptions(as_of=ASOF,budget=3,allowed_from=date(2028,1,2)),direct_cells=[(1,ORIGIN)])
        self.assertEqual(r.status,'INFEASIBLE')

    def test_unknown_is_not_infeasible(self):
        data,codes=fixture();new=replace(data,requests=(Request(1,ORIGIN,'DT'),))
        r=repair_schedule(new,codes,options=RepairOptions(as_of=ASOF,budget=0.00001),direct_cells=[(1,ORIGIN)])
        self.assertEqual(r.status,'UNKNOWN')

    def test_validator_rejects_wrong_score(self):
        data,codes=fixture()
        with self.assertRaisesRegex(ValueError,'khớp'):
            validate_repair(data,codes,codes,as_of=ASOF,allowed=month_days(data.year,data.month),expected=[999])

    def test_validator_rejects_hard_violation(self):
        data,codes=fixture();bad=dict(codes);bad[1,ORIGIN]='OFF'
        with self.assertRaises(ValueError):validate_repair(data,codes,bad,as_of=ASOF,allowed=month_days(data.year,data.month))

    def test_cancellation_is_distinct(self):
        data,codes=fixture();new=replace(data,requests=(Request(1,ORIGIN,'DT'),))
        r=repair_schedule(new,codes,options=RepairOptions(as_of=ASOF,budget=2),direct_cells=[(1,ORIGIN)],cancelled=lambda:True)
        self.assertEqual(r.status,'CANCELLED')

    def test_boundary_detects_violation(self):
        a,ac=fixture(2028,1);b,bc=fixture(2028,2)
        report=boundary_report(a,ac,b,bc);self.assertEqual(report['errors'],0)
        bad=dict(ac)
        who=next(i for (i,d),c in bc.items() if d==date(2028,2,1) and c=='1')
        for d in (date(2028,1,29),date(2028,1,30),date(2028,1,31)):bad[who,d]='1'
        self.assertGreater(boundary_report(a,bad,b,bc)['errors'],0)

    def test_missing_history_never_filled_off(self):
        data,codes=fixture();bad=replace(data,previous={})
        self.assertTrue(history_gaps(bad))
        with self.assertRaises(ValueError):boundary_report(bad,{},*fixture(2028,2))


class AnnualTests(DBCase):
    def test_failure_in_second_month_saves_no_partial_versions(self):
        with self.service.sessions.begin() as s:
            plan=m.AnnualPlan(name='Atomic failure test',year=2028,assumptions='Explicit synthetic input')
            s.add(plan);s.flush();pid=plan.id
            a,_=fixture(2028,1,fixed=True);b,_=fixture(2028,2,fixed=True)
            seed_month(s,a,plan_id=pid)
            rules=deepcopy(b.rules);rules['credit_policy']['demand_staff_per_shift']['1']=4
            seed_month(s,replace(b,rules=rules),plan_id=pid)
        job=self.service.enqueue(JobInput(revision=1,mode='ANNUAL',annual_plan_id=pid,budget=12))
        execute(self.engine,job['id'])
        result=self.service.job(job['id'])
        self.assertEqual(result['status'],'INFEASIBLE',result['error'])
        with self.service.sessions() as s:self.assertEqual(s.scalar(select(func.count()).select_from(m.ScheduleVersion)),0)

    def test_real_solver_twelve_month_leap_year_and_atomic_v0(self):
        with self.service.sessions.begin() as s:
            plan=m.AnnualPlan(name='TEST 2028: explicit synthetic, no holidays',year=2028,
                assumptions='18 single-shift leaders, 3 on / 3 off; max night=3 test-only; all days fixed for reproducible feasibility.')
            s.add(plan);s.flush();pid=plan.id
            for month in range(1,13):
                data,_=fixture(2028,month,fixed=True)
                seed_month(s,data,plan_id=pid)
        j=self.service.enqueue(JobInput(revision=1,mode='ANNUAL',annual_plan_id=pid,budget=45))
        execute(self.engine,j['id']);job=self.service.job(j['id'])
        self.assertEqual(job['status'],'SUCCEEDED',job['error'])
        ids=job['result']['version_ids'];self.assertEqual(len(ids),12)
        self.assertEqual(len(job['result']['boundaries']),11)
        self.assertTrue(all(b['errors']==0 for b in job['result']['boundaries']))
        with self.service.sessions() as s:
            feb=s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==2028,m.PlanningMonth.month==2))
            vid=next(v for v in ids if s.get(m.ScheduleVersion,v).month_id==feb.id)
            self.assertEqual(len(codes_for(s,vid)),18*29)
            self.assertIsNone(feb.current_version_id)
        # A malformed acceptance rolls back all months, without a partial update.
        with self.assertRaises(ValueError):confirm_plan(self.service,pid,ids[:-1],1)
        with self.service.sessions() as s:
            self.assertEqual(revision(s),1)
            self.assertFalse(any(x.current_version_id for x in s.scalars(select(m.PlanningMonth))))
        confirm_plan(self.service,pid,ids,1)
        stats=annual_statistics(self.service,pid)
        self.assertTrue(stats['plan']['baseline_frozen']);self.assertEqual(len(stats['personal']),18*366)
        with self.assertRaises(Conflict):confirm_plan(self.service,pid,ids,2)

    def test_cross_month_repair_fails_closed(self):
        with self.service.sessions.begin() as s:
            a,ac=fixture(2028,1);b,bc=fixture(2028,2)
            mid=seed_month(s,a,ac);seed_month(s,b,bc)
        req=self.service.request_batch(RequestBatch(revision=1,employee_ids=[1],from_date=date(2028,1,31),to_date=date(2028,2,1),code='DT'))
        with self.assertRaises(Unsupported):
            self.service.enqueue(JobInput(revision=2,mode='REPAIR',month_id=mid,request_ids=[r['id'] for r in req['requests']],as_of=ASOF,budget=2))
        with self.service.sessions() as s:self.assertEqual(s.scalar(select(func.count()).select_from(m.ScheduleVersion)),2)


class ApiTests(DBCase):
    def test_staffing_schema_rejects_missing_fields(self):
        self.seed()
        with TestClient(create_app(self.path,start_workers=False)) as c:
            r=c.put('/api/months/1/configuration',json={'revision':1,'holidays':[],'demands':[{'day':'2028-01-01'}]})
            self.assertEqual(r.status_code,422)

    def test_validation_hours_are_explicit_and_versioned(self):
        self.seed()
        with TestClient(create_app(self.path,start_workers=False)) as c:
            body={'revision':1,'policy':{'min_rest_hours':0,'codes':{'CT':{'kind':'work','start':'08:00','end':'12:00'},
                'DT':{'kind':'work','start':'08:00','end':'12:00'},'T':{'kind':'work','start':'08:00','end':'16:00'},'BKD':{'kind':'rest'}}}}
            r=c.put('/api/months/1/validation-work-policy',json=body)
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['scope'],'VALIDATOR_ONLY')
            self.assertEqual(c.get('/api/months/1/rules').json()['profile']['work_policy']['min_rest_hours'],0)
            body['revision']=2;body['policy']['codes']['BKD']={'kind':'work','start':'08:00','end':'12:00'}
            self.assertEqual(c.put('/api/months/1/validation-work-policy',json=body).status_code,422)

    def test_api_validation_overlay_and_docs(self):
        _,_,mid=self.seed()
        app=create_app(self.path,start_workers=False)
        with TestClient(app) as c:
            self.assertEqual(c.get('/openapi.json').status_code,200)
            self.assertEqual(c.post('/api/jobs',json={'mode':'BAD','revision':1}).status_code,422)
            r=c.post('/api/requests/batch',json={'revision':1,'employee_ids':[1],'from_date':str(ORIGIN),'to_date':str(ORIGIN),'code':'DT'})
            self.assertEqual(r.status_code,200,r.text)
            cal=c.get(f'/api/months/{mid}/calendar').json()
            self.assertEqual(cal['requests'][0]['code'],'DT')
            self.assertEqual(cal['assignments'][0]['code'],'1')
            self.assertEqual(c.post('/api/requests/batch',json={'revision':1,'employee_ids':[1],'from_date':str(ORIGIN),'to_date':str(ORIGIN),'code':'OFF'}).status_code,409)

    def test_cross_origin_writes_rejected(self):
        with TestClient(create_app(self.path,start_workers=False)) as c:
            self.assertEqual(c.post('/api/jobs',headers={'Origin':'https://untrusted.example'},json={}).status_code,403)


if __name__=='__main__':unittest.main()
