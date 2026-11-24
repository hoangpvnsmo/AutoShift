"""Mandatory pre-persistence gate; no CP-SAT dependency and no solver mutation."""
from dataclasses import dataclass
from core.engine.checkpoint import write_checkpoint
from .quality import validate_quality, score_vector
from .result import ValidationReport
from .structure import checkpoint_cells


class ValidationRejected(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class GateResult:
    report: ValidationReport
    status: str  # VERIFIED or LIMITED (B_WORK_TIME only)


def check_candidate(data, prefs, candidate, codes, *, work_policy=None, require_complete=False):
    """Do not claim optimality or publication approval; compare every objective."""
    try:
        expected = {(i,d): c for i,d,c in checkpoint_cells(data,candidate.assignments)}
        if dict(codes) != expected:
            raise ValidationRejected('Mã lịch báo cáo không khớp phân công và Requests.')
        report = validate_quality(data, codes, prefs=prefs, source='engine_candidate',
                                  expected_scores=candidate.scores, work_policy=work_policy)
        if report.errors:
            raise ValidationRejected(f'Validator phát hiện {len(report.errors)} lỗi cứng.', report)
        pending = set(report.hard_pending)
        allowed = {'B_WORK_TIME'} if work_policy is None and not require_complete else set()
        if pending - allowed:
            raise ValidationRejected('Rule cứng chưa kiểm tra: '+', '.join(sorted(pending)), report)
        actual = score_vector(report)
        if actual is None or actual != candidate.scores:
            raise ValidationRejected(f'Điểm độc lập không khớp checkpoint: {actual!r} != {candidate.scores!r}', report)
        return GateResult(report, 'LIMITED' if pending else 'VERIFIED')
    except ValidationRejected:
        raise
    except Exception as exc:
        raise ValidationRejected(f'Không thể hoàn thành Validator: {type(exc).__name__}: {exc}') from exc


def validate_and_persist(data, prefs, candidate, codes, *, path=None, chosen=None,
                         work_policy=None, require_complete=False, emit=print):
    """Check even for stateless runs. A rejected candidate never reaches writer."""
    gate = check_candidate(data, prefs, candidate, codes, work_policy=work_policy,
                           require_complete=require_complete)
    if chosen is not None:
        # choose_checkpoint may only change proof metadata, never the schedule/scores.
        if (chosen.assignments != candidate.assignments or chosen.scores != candidate.scores
                or chosen.base_hash != candidate.base_hash or chosen.pref_hash != candidate.pref_hash):
            raise ValidationRejected('Checkpoint được chọn khác nghiệm đã kiểm tra.', gate.report)
    emit(f'Independent Validator: {gate.status} | ERROR=0 | hard pending={len(gate.report.hard_pending)} | điểm 9/9 khớp')
    for rule in gate.report.hard_pending:
        emit('  NOT_CHECKED:', rule)
    for f in gate.report.warnings:
        emit(f'  WARNING {f.rule_id}: ID={f.employee_id} ngày={f.day} {f.message}')
    if path is not None and chosen is not None:
        write_checkpoint(path, chosen)
    return gate
