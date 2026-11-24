"""Hợp đồng kết quả Validator: tách mức độ khỏi tình trạng thực thi."""
from dataclasses import dataclass, asdict
from datetime import date
from enum import Enum
from .catalog import BY_ID, RULES, Kind

class Status(str, Enum):
    PASS='PASS'
    FAIL='FAIL'
    NOT_CHECKED='NOT_CHECKED'
    NOT_APPLICABLE='NOT_APPLICABLE'
    MEASURED='MEASURED'

class Severity(str, Enum):
    ERROR='ERROR'
    WARNING='WARNING'
    INFO='INFO'

@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    message: str
    employee_id: int | None = None
    day: date | None = None
    shift: int | None = None
    expected: str | None = None
    actual: str | None = None
    related_day: date | None = None

    def __post_init__(self):
        if self.rule_id not in BY_ID: raise ValueError('Mã rule không tồn tại.')
        if not isinstance(self.severity,Severity): raise TypeError('severity phải là Severity.')
        if not self.message.strip(): raise ValueError('Thiếu mô tả.')

@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    status: Status
    note: str
    findings: tuple[Finding,...] = ()
    score: tuple[int,...] | None = None

    def __post_init__(self):
        spec=BY_ID[self.rule_id]
        if not isinstance(self.status,Status): raise TypeError('status phải là Status.')
        if not self.note.strip(): raise ValueError('Thiếu ghi chú kiểm tra.')
        if any(f.rule_id!=self.rule_id for f in self.findings): raise ValueError('Finding không khớp rule.')
        has_error=any(f.severity==Severity.ERROR for f in self.findings)
        if has_error!=(self.status==Status.FAIL): raise ValueError('FAIL phải có ERROR, ERROR phải có FAIL.')
        if self.status==Status.FAIL and spec.kind!=Kind.HARD: raise ValueError('Mục tiêu mềm không phải lỗi cứng.')
        if self.status==Status.MEASURED:
            if spec.kind==Kind.HARD or self.score is None: raise ValueError('MEASURED chỉ cho mục tiêu/thống kê có điểm.')
        elif self.score is not None: raise ValueError('Chỉ MEASURED mới có điểm.')
        if self.status==Status.PASS and spec.kind!=Kind.HARD: raise ValueError('Mục tiêu mềm dùng MEASURED, không dùng PASS.')
        if self.status==Status.NOT_APPLICABLE and spec.kind==Kind.HARD:
            # Conditional hard checks need an explicit reason and an actual decision by their checker.
            # In 5.1 no hard rule is declared inapplicable by the registry itself.
            if self.rule_id not in {'D06','D_ALLOWED','D_T','B_WORK_TIME'}: raise ValueError('Rule cứng này không được bỏ qua.')

@dataclass(frozen=True)
class ValidationReport:
    year: int
    month: int
    results: tuple[RuleResult,...]
    source: str = 'codes'

    def __post_init__(self):
        if tuple(x.rule_id for x in self.results)!=tuple(x.id for x in RULES):
            raise ValueError('Báo cáo phải chứa đúng toàn bộ rule theo danh mục.')

    @property
    def errors(self): return tuple(f for x in self.results for f in x.findings if f.severity==Severity.ERROR)
    @property
    def warnings(self): return tuple(f for x in self.results for f in x.findings if f.severity==Severity.WARNING)
    @property
    def hard_pending(self): return tuple(x.rule_id for x in self.results if BY_ID[x.rule_id].kind==Kind.HARD and x.status==Status.NOT_CHECKED)
    @property
    def verdict(self):
        if self.errors: return 'ERROR'
        if self.hard_pending: return 'INCOMPLETE'
        return 'WARNING' if self.warnings else 'PASS'
    @property
    def hard_checks_complete(self): return not self.hard_pending
    def to_dict(self):
        return {'year':self.year,'month':self.month,'source':self.source,'verdict':self.verdict,
                'hard_checks_complete':self.hard_checks_complete,'hard_pending':self.hard_pending,
                'errors':len(self.errors),'warnings':len(self.warnings),'rules':[asdict(x) for x in self.results]}
