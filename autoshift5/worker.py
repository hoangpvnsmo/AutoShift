"""One owned process per durable job. No database transaction spans a solve."""
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4
import json
import sys
import traceback
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker
from core.engine.autoshift_engine import run_schedule
from core.engine.engine_config import EngineOptions, STAGES
from core.engine.checkpoint import Checkpoint, fingerprints, write_checkpoint, active_vector
from core.engine.objective_engine import active_layers
from core.data.calendar_utils import month_days
from validator.quality import score_vector
from . import models as m
from .db import make_engine, ROOT, revision, audit
from .domain import deserialize, serialize, numeric, now, history_length, history_gaps, Conflict, BoundaryConflict
from .repository import require, codes_for, save_version, snapshot, application_sources
from .repair import repair_schedule, RepairOptions
from .validation import boundary_report, validate_month
from .services import Service


class Cancelled(Exception):
    pass


class NoSolution(Exception):
    def __init__(self,status,message):
        super().__init__(message)
        self.status=status


def execute(engine, job_id):
    with sessionmaker(engine)() as check:
        linked=require(check,m.SolverRun,job_id).options.get('cascade',False)
    if linked:
        from .cascade_worker import execute_cascade
        return execute_cascade(engine,job_id)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions.begin() as s:
        job = require(s, m.SolverRun, job_id)
        if job.cancel_requested or job.status=='CANCELLED':
            job.status, job.finished_at = 'CANCELLED', now()
            return
        job.status, job.started_at = 'RUNNING', now()
        options, mode, input_revision = dict(job.options), job.mode, job.revision
        specs = []
        for sid in job.snapshots:
            snap = require(s, m.InputSnapshot, sid)
            specs.append(dict(id=sid, month_id=snap.month_id, raw=snap.payload, policy=snap.work_policy, sources=snap.source_hashes))
        baseline_id = options.get('baseline_version_id')
        baseline = codes_for(s, baseline_id) if baseline_id else None
        baseline_meta = None
        if baseline_id:
            v = require(s, m.ScheduleVersion, baseline_id)
            bs = require(s, m.InputSnapshot, v.snapshot_id)
            baseline_meta = dict(proof=v.proof, raw=bs.payload, policy=bs.work_policy, status=v.status, sources=bs.source_hashes)
        annual_codes = None
        if len(specs)==1:
            month = require(s, m.PlanningMonth, specs[0]['month_id'])
            if month.baseline_version_id:
                annual_codes = codes_for(s, month.baseline_version_id)

    def is_cancelled():
        with sessions() as s:
            j = s.get(m.SolverRun, job_id)
            return j is None or j.cancel_requested or j.status not in ('QUEUED','RUNNING')

    def emit(*parts):
        message = ' '.join(map(str, parts))
        with sessions.begin() as s:
            j = require(s, m.SolverRun, job_id)
            if j.cancel_requested or j.status!='RUNNING':
                raise Cancelled()
            j.log = (j.log+'\n'+message)[-200000:]
            j.stage = message[:150]

    results = []
    cumulative = {}
    deadline = perf_counter()+float(options['budget'])
    balance_report=None
    planning_deadline=deadline-float(options['budget'])*.4 if mode=='ANNUAL' and options.get('annual_balance',True) and len(specs)>1 else deadline
    try:
        previous_result = None
        for index, spec in enumerate(specs):
            if is_cancelled():
                raise Cancelled()
            data, prefs = deserialize(spec['raw'])
            if previous_result:
                prev_codes = previous_result['codes']
                first = month_days(data.year, data.month)[0]
                from datetime import timedelta
                needed = {(e.id, first-timedelta(days=k)) for e in data.employees for k in range(1, history_length(data)+1)}
                if not needed <= set(prev_codes):
                    raise ValueError('ANNUAL thiếu lịch sử ở biên hoặc có nhân sự mới chưa khai báo lịch sử.')
                data = replace(data, previous={k: prev_codes[k] for k in sorted(needed)})
            if mode=='ANNUAL' and history_gaps(data):
                raise ValueError('ANNUAL thiếu lịch sử cho E05 ở tháng đầu; khai báo rõ ít nhất 6 ngày, không tự điền OFF.')
            budget = max(.01, (planning_deadline-perf_counter())/(len(specs)-index))
            emit(f'{data.year}-{data.month:02d}: bắt đầu {mode}, ngân sách còn {budget:.2f}s')
            repair = None
            if mode=='REPAIR':
                with sessions() as s:
                    requests = [require(s, m.Request, rid) for rid in options.get('request_ids', [])]
                    cells = {(r.employee_id, r.day) for r in requests}
                raw_opts = dict(as_of=datetime.fromisoformat(options['as_of']), budget=budget,
                    mode=options['repair_mode'], max_changes=options.get('max_changes'), no_admin_increase=options['no_admin_increase'],
                    no_overtime_increase=options['no_overtime_increase'], disabled_layers=tuple(options.get('disabled_layers', [])),
                    published=baseline_meta['status']=='PUBLISHED',
                    allowed_from=date.fromisoformat(options['allowed_from']) if options.get('allowed_from') else None,
                    allowed_until=date.fromisoformat(options['allowed_until']) if options.get('allowed_until') else None)
                locks = {(r['employee_id'], date.fromisoformat(r['day'])) for r in spec['raw']['_context']['locks']}
                if baseline_meta['status']=='ACTUAL':
                    locks.update(baseline)
                def check_boundary(codes):
                    with sessions() as s:
                        return Service(engine)._check_neighbors(s, data, codes)
                r = repair_schedule(data, baseline, options=RepairOptions(**raw_opts), direct_cells=cells, locks=locks,
                                    annual=annual_codes, prefs=prefs, work_policy=spec['policy'], emit=emit, cancelled=is_cancelled,
                                    boundary_check=check_boundary)
                if r.status=='CANCELLED':
                    raise Cancelled()
                if r.codes is None:
                    with sessions.begin() as s:
                        j = require(s, m.SolverRun, job_id)
                        j.status, j.result, j.finished_at = r.status, {'attempts': r.attempts, 'version_ids': []}, now()
                    return
                codes, report, proof, repair = r.codes, r.report, r.proof, {**r.metrics, 'attempts': r.attempts}
                quality = score_vector(report)
            else:
                cp_path = ROOT/'data'/'jobs'/job_id/f'{spec["month_id"]}.json'
                fresh = mode in ('FRESH', 'ANNUAL')
                if not fresh and baseline is not None:
                    old_data, old_prefs = deserialize(baseline_meta['raw'])
                    old_report = validate_month(old_data, baseline, old_prefs, baseline_meta['policy'])
                    prefix = int(baseline_meta['proof'].get('engine_prefix', 0))
                    if baseline_meta['sources'] != application_sources():
                        prefix = 0
                    cp = Checkpoint(*fingerprints(old_data, old_prefs, ROOT), numeric(baseline), score_vector(old_report), prefix)
                    write_checkpoint(cp_path, cp)
                best = None
                stalls = 0
                max_passes = options.get('passes', 1) if mode=='RESUME' else 1
                pass_end = min(deadline, perf_counter()+budget)
                for turn in range(max_passes):
                    remain = pass_end-perf_counter()
                    if remain <= .05 or is_cancelled():
                        break
                    per_pass = remain/(max_passes-turn) * (.8 if mode=='ANNUAL' else 1)
                    active = active_layers(data)
                    count = max(1, sum(active[1:]))
                    base_time = per_pass*.5 if count else per_pass
                    rest = per_pass*.5/count
                    kw = {specification.budget: max(.01, rest) for specification in STAGES[1:]}
                    opts = EngineOptions(base_time=max(.01, base_time), **kw, ideal_time=0,
                            validation_time=min(5, max(.01, per_pass*.05)), fresh=fresh and turn==0, lns_seed=turn+1)
                    emit(f'Lượt RESUME {turn+1}/{max_passes}' if mode=='RESUME' else 'Giải mô hình tháng')
                    result = run_schedule(data, prefs, root=ROOT, options=opts,
                                          checkpoint_file=cp_path, emit=emit, work_policy=spec['policy'])
                    if result.codes is None:
                        if best is None:
                            raise NoSolution(result.status,f'{result.status}: chưa có nghiệm; không cập nhật lịch.')
                        break
                    vector = active_vector(result.report.scores, active)
                    if best is None or vector < active_vector(best.report.scores, active):
                        best, stalls = result, 0
                    else:
                        stalls += 1
                    if result.quality.proven or stalls>=2:
                        break
                    fresh = False
                if best is None:
                    if is_cancelled():
                        raise Cancelled()
                    raise NoSolution('UNKNOWN','UNKNOWN: hết ngân sách trước khi có nghiệm đã được kiểm tra.')
                codes, report = best.codes, best.validation.report
                quality = score_vector(report)
                proof = dict(status=best.status, engine_prefix=best.candidate.proven_prefix,
                             reused_prefix=best.reused_prefix, completed_passes=turn+1, all_active_proven=best.quality.proven,
                             engine_hashes=list(fingerprints(data, prefs, ROOT)))
                for key in ('e01','e02','holiday','weekend','rest_a','rest_b','rotation','preferences'):
                    layer=getattr(best.quality,key)
                    emit(key,layer.status,layer.before,'->',layer.after,layer.note)
                if mode=='ANNUAL':
                    from .annual_fairness import refine_annual, counts_for, eligibility_record
                    if options.get('annual_balance',True):
                        annual_fairness={'status':'DEFERRED_TO_YEAR_BALANCE','policy':'Dành 40% ngân sách cho cân toàn năm trước khi lưu V0.'}
                    else:
                        codes,report,annual_fairness=refine_annual(data,prefs,codes,report,cumulative,
                            budget=max(0,pass_end-perf_counter()),work_policy=spec['policy'],emit=emit)
                    quality=score_vector(report)
                    proof['annual_fairness_optimization']=annual_fairness
                    for i,row in counts_for(data,codes).items():
                        target=cumulative.setdefault(i,{})
                        for metric,value in row.items():target[metric]=target.get(metric,0)+value
                        person=next(e for e in data.employees if e.id==i)
                        target['_eligibility_history']=(*target.get('_eligibility_history',()),eligibility_record(data,person))
            boundary = None
            if previous_result:
                boundary = boundary_report(previous_result['data'], previous_result['codes'], data, codes, prefs=prefs, work_policy=spec['policy'])
                if boundary['errors']:
                    raise ValueError('ANNUAL lỗi biên tháng; không lưu một phần.')
            elif mode!='ANNUAL':
                with sessions() as s:
                    Service(engine)._check_neighbors(s, data, codes)
            entry = dict(spec=spec, data=data, prefs=prefs, codes=codes, report=report.to_dict(), proof=proof,
                         quality=quality, repair=repair, boundary=boundary)
            results.append(entry)
            previous_result = entry
        if mode=='ANNUAL' and options.get('annual_balance',True):
            from .year_balance import balance_year
            from .cascade_model import CascadeFrame
            from .year_metrics import YearMetrics
            from datetime import timedelta
            frames=[];frontier=None
            for r in results:
                context=r['spec']['raw']['_context']
                locks=tuple((l['employee_id'],date.fromisoformat(l['day'])) for l in context.get('locks',[]))
                frames.append(CascadeFrame(r['spec']['month_id'],r['data'],r['prefs'],r['codes'],None,
                    locks=locks,work_policy=r['spec']['policy'],fixed_data=r['data'],fixed_prefs=r['prefs'],fixed_policy=r['spec']['policy']))
            nextday=month_days(frames[-1].data.year,frames[-1].data.month)[-1]+timedelta(days=1)
            with sessions() as s:
                other=s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==nextday.year,m.PlanningMonth.month==nextday.month))
                if other and other.current_version_id:
                    v=require(s,m.ScheduleVersion,other.current_version_id);snap=require(s,m.InputSnapshot,v.snapshot_id)
                    fd,fp=deserialize(snap.payload)
                    frontier=CascadeFrame(other.id,fd,fp,codes_for(s,v.id),v.id,status=v.status,
                        fixed_data=fd,fixed_prefs=fp,fixed_policy=snap.work_policy)
            remaining=deadline-perf_counter()
            if remaining>.05:
                balanced=balance_year(frames,frontier,RepairOptions(as_of=datetime.fromisoformat(options['as_of']) if options.get('as_of') else now(),
                    budget=remaining),window_months=options.get('balance_window_months',3),emit=emit,cancelled=is_cancelled)
                if balanced.status=='CANCELLED':raise Cancelled()
                if not balanced.entries:raise NoSolution(balanced.status,'Chưa có phương án cân năm đã kiểm tra.')
                balance_report={**balanced.metrics['annual_balance'],'proof':balanced.proof,'attempts':balanced.attempts}
                for r,e in zip(results,balanced.entries):
                    original_proof=r['proof']
                    r.update(data=e['data'],codes=e['codes'],report=e['report'].to_dict(),quality=e['quality'],
                        proof=dict(status='FEASIBLE',engine_prefix=0,reused_prefix=0,all_active_proven=False,
                            monthly_before_balance=original_proof,annual_balance=balanced.proof))
                for j,r in enumerate(results):
                    r['boundary']=balanced.boundaries[j-1] if j else None
            else:
                context=YearMetrics(frames,frontier);flat={k:c for f in frames for k,c in f.baseline.items()};report=context.report(flat)
                balance_report=dict(before=report,after=report,status='NOT_RUN_BUDGET_EXHAUSTED')
                emit('Chưa chạy cân năm: hết ngân sách sau khi lập các tháng. Kết quả chưa được coi là đã cân năm.')
        if is_cancelled():
            raise Cancelled()
        with sessions.begin() as s:
            # Acquire the write reservation before checking revision and version pointers.
            s.execute(update(m.State).where(m.State.id==1).values(revision=m.State.revision))
            if revision(s) != input_revision:
                raise Conflict('STALE: dữ liệu thay đổi trong lúc Solver chạy; kết quả không được lưu/áp dụng.')
            j = require(s, m.SolverRun, job_id)
            if j.cancel_requested or j.status!='RUNNING':
                raise Cancelled()
            ids = []
            for r in results:
                month = require(s, m.PlanningMonth, r['spec']['month_id'])
                if month.current_version_id != r['spec']['raw']['_context']['current_version']:
                    raise Conflict('Phiên bản áp dụng đã thay đổi trong lúc giải.')
                snap = require(s, m.InputSnapshot, r['spec']['id'])
                if mode=='ANNUAL':
                    snap = snapshot(s, month.id, data=r['data'], prefs=r['prefs'], context=r['spec']['raw']['_context'])
                v = save_version(s, month.id, snap, r['codes'], r['report'], source=mode,
                    parent=(baseline_id or month.current_version_id) if len(results)==1 else month.current_version_id, proof=r['proof'],
                    scores=r['quality'], repair=r['repair'])
                ids.append(v.id)
            j.status, j.finished_at, j.stage = 'SUCCEEDED', now(), 'Hoàn tất: bản nháp chờ duyệt'
            j.result = dict(version_ids=ids, repair=results[0]['repair'] if len(results)==1 else None,
                            boundaries=[r['boundary'] for r in results if r['boundary']], elapsed_budget=options['budget']-(deadline-perf_counter()),
                            publication='DRAFT_ONLY')
            if balance_report:j.result['annual_balance']=balance_report
            audit(s, 'SOLVER_RESULT', job_id, {'version_ids': ids})
    except Cancelled:
        with sessions.begin() as s:
            j = require(s, m.SolverRun, job_id)
            j.status, j.finished_at, j.stage = 'CANCELLED', now(), 'Đã hủy; lịch áp dụng giữ nguyên'
    except Exception as exc:
        with sessions.begin() as s:
            j = require(s, m.SolverRun, job_id)
            j.status = 'BOUNDARY_CONFLICT' if isinstance(exc,BoundaryConflict) else 'STALE' if isinstance(exc, Conflict) else exc.status if isinstance(exc,NoSolution) else 'FAILED'
            j.error, j.finished_at = str(exc), now()
            j.stage='Không lưu: xung đột biên tháng.' if isinstance(exc,BoundaryConflict) else str(exc)[:150]
            if isinstance(exc,BoundaryConflict):
                j.result={'version_ids':[],'boundary_conflict':exc.details}
            j.log = (j.log+'\n'+traceback.format_exc())[-200000:]


if __name__=='__main__':
    execute(make_engine(Path(sys.argv[1])), sys.argv[2])
