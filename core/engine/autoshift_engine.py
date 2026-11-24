"""Public application service for CLI, future UI and later repair workflow."""
from dataclasses import dataclass
from pathlib import Path
from core.model.engine_model import build_stage3
from core.engine.engine_config import EngineOptions
from core.engine.objective_engine import ObjectiveResult, solve_objectives, make_checkpoint, active_layers
from core.engine.engine_checkpoint import migrate_checkpoint, choose_checkpoint
from core.engine.checkpoint import fingerprints, read_checkpoint, Checkpoint
from core.reporting.schedule_report import ScheduleReport, collect_report
from core.objectives.preference_checks import validate_preferences
from core.data.calendar_utils import standard_credit
from core.objectives.day_balance_checks import day_sets
from validator.engine_gate import GateResult, validate_and_persist, check_candidate

@dataclass(frozen=True)
class EngineResult:
    quality: ObjectiveResult
    days: tuple
    report: ScheduleReport | None
    checkpoint_path: Path | None
    checkpoint_action: str
    candidate: Checkpoint | None
    old_vector: tuple | None
    new_vector: tuple | None
    reused_prefix: int
    migrated_from: str | None
    validation: GateResult | None = None

    @property
    def assignments(self): return self.quality.selected.assignments
    @property
    def codes(self): return self.report.codes if self.report is not None else None
    @property
    def status(self):
        if self.assignments is None: return self.quality.selected.status
        return 'OPTIMAL' if self.quality.proven else 'FEASIBLE'


def run_schedule(data,prefs=(),*,root,options=None,checkpoint_file=None,emit=print,
                 work_policy=None,require_complete=False):
    """One call builds a new model, solves, validates, then atomically saves.
    Pass checkpoint_file=None for a stateless run. Caller must choose a real/demo
    path consistent with data (CLI does this). No input files are modified.
    """
    root=Path(root); opts=options if options is not None else EngineOptions()
    prefs=validate_preferences(data,tuple(prefs))
    ctx,demand,policy,credit=build_stage3(data)
    emit('Số nguyện vọng mềm:',len(prefs))
    emit('Chế độ DEMAND_BASED | ADMIN:', 'BẬT' if policy.enabled else 'TẮT')
    emit('Công chuẩn tháng:',standard_credit(data),'| Nhu cầu trực:',sum(demand.values()))
    emit('ADMIN ngày lễ:',policy.allow_holiday,'| Giới hạn ca/người:',policy.max_shifts)
    h,w=day_sets(data,ctx.days); emit('Ngày lễ:',len(h),'| Cuối tuần không trùng lễ:',len(w))
    path=Path(checkpoint_file) if checkpoint_file is not None else None
    hashes=fingerprints(data,prefs,root); saved=None
    emit('Checkpoint:',path if path is not None else '(tắt)','| Chế độ:', 'FRESH' if opts.fresh else 'RESUME')
    if path is not None:
        try: saved=read_checkpoint(path)
        except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
            emit('CẢNH BÁO: checkpoint lỗi; không dùng:',exc)
        if saved is None: emit('Chưa có checkpoint sử dụng được; giải mới.')
    saved,migrated=migrate_checkpoint(saved,data,prefs,root,hashes)
    if migrated:
        label='5.6' if migrated=='56' else '4.8' if migrated=='48' else f'4.7.{migrated[-1]}'
        emit(f'Checkpoint {label}: hash nền khớp; kiểm tra lại trước khi tái sử dụng chứng minh.')
    emit('LNS E05/E06:', 'TẮT' if opts.search_mode=='GLOBAL' else f'{"THÍCH NGHI" if opts.adaptive else "CỐ ĐỊNH"} | quota đầu {opts.lns_fraction:.0%} | {opts.lns_slice:g}s/vùng | seed {opts.lns_seed}')
    run=solve_objectives(ctx,data,credit,demand,prefs,options=opts,saved=saved,hashes=hashes,emit=emit)
    q=run.quality; reused=0 if opts.fresh else run.old_prefix
    if q.selected.assignments is None:
        return EngineResult(q,tuple(ctx.days),None,path,'NO_SOLUTION',None,None,None,reused,migrated)
    # Perform all report checks BEFORE changing the checkpoint. The report is
    # reusable by a future GUI/exporter, which need not parse terminal strings.
    report=collect_report(data,ctx.days,demand,q.selected.assignments,prefs,q)
    candidate=make_checkpoint(ctx,data,prefs,q,hashes)
    if candidate.scores!=report.scores: raise RuntimeError('Điểm checkpoint không khớp báo cáo.')
    chosen,action,old_vector,new_vector=choose_checkpoint(candidate,run.old_scores if run.old_valid else None,run.old_prefix,active_layers(data),opts.fresh)
    # FRESH can keep an older checkpoint; independently check it as well before
    # calling it the protected incumbent. No automatic fallback on rejection.
    if path is not None and action=='KEPT_OLD':
        from dataclasses import replace
        from validator.structure import checkpoint_cells
        old=replace(saved,scores=run.old_scores)
        old_codes={(i,d):c for i,d,c in checkpoint_cells(data,old.assignments)}
        check_candidate(data,prefs,old,old_codes,work_policy=work_policy,require_complete=require_complete)
    gate=validate_and_persist(data,prefs,candidate,report.codes,path=path,chosen=chosen,
        work_policy=work_policy,require_complete=require_complete,emit=emit)
    if path is None: action='NOT_REQUESTED'
    # Keep candidate's proven prefix consistent with any proof retained on ties.
    return EngineResult(q,tuple(ctx.days),report,path,action,chosen if chosen is not None else candidate,old_vector,new_vector,reused,migrated,gate)
