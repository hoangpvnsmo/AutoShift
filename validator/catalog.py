"""Danh mục rule được đối chiếu với AutoShift 4.8; không phụ thuộc CP-SAT."""
from dataclasses import dataclass
from enum import Enum

class Kind(str, Enum):
    HARD = 'HARD'
    OBJECTIVE = 'OBJECTIVE'
    INFO = 'INFO'

class Coverage(str, Enum):
    EXISTING = 'EXISTING_4_8'
    PARTIAL = 'PARTIAL_4_8'
    MISSING = 'MISSING_4_8'
    INFRA = 'NEW_5_1'

@dataclass(frozen=True)
class RuleSpec:
    id: str
    kind: Kind
    title: str
    criterion: str
    source: tuple[str, ...]
    coverage: Coverage
    step: str
    flag: str | None = None
    legacy_ids: str = ''

H,O,I=Kind.HARD,Kind.OBJECTIVE,Kind.INFO
E,P,M,N=Coverage.EXISTING,Coverage.PARTIAL,Coverage.MISSING,Coverage.INFRA

def r(id,kind,title,criterion,source,coverage,step,flag=None,legacy_ids=''):
    return RuleSpec(id,kind,title,criterion,tuple(source.split(';')),coverage,step,flag,legacy_ids)

RULES = (
    r('INPUT_GRID',H,'Cấu trúc lịch','Đúng một mã 1/2/3/OFF/CT/DT/BKD/T cho từng ID và ngày trong tháng; không thiếu, thừa hoặc trùng.', 'core/reporting/schedule_report.py:collect_report;core/data/data_loader.py:CODES',N,'5.1'),
    r('INPUT_HISTORY',H,'Đủ lịch sử','Đủ max(giới hạn đêm, giới hạn ngày trực, 1) ngày trước tháng, mọi ID có mã hợp lệ; không mặc định OFF.', 'core/data/data_loader.py:load_data;core/validation/sequence_checks.py:history_codes',E,'5.1'),
    r('INPUT_CONFIG',H,'Dữ liệu và cấu hình hợp lệ','Kiểm tra ID, quyền ca, ngày lễ, Requests, Preferences, cấu hình nhu cầu, giờ ca, công và ADMIN; không suy diễn cấu hình thiếu.', 'core/data/data_loader.py:load_data;core/validation/credit_checks.py:request_map;core/validation/admin_checks.py:admin_policy;core/validation/transition_checks.py:transition_config;core/objectives/preference_checks.py:validate_preferences',E,'5.2'),
    r('A01',H,'Ba loại ca mỗi ngày','Kiểm tra đủ ba ca 1/2/3 trên mọi ngày theo lịch tháng.', 'core/model/model_builder.py:build_model;core/validation/credit_checks.py:check_staffing',E,'5.2'),
    r('A02',H,'Số người tối thiểu','Số người mỗi ca >= staff_per_shift[ca].min.', 'core/constraints/hard_constraints.py:add_staffing_constraints',E,'5.2'),
    r('A03',H,'Phụ trách ca','Số phụ trách >= min_leaders_per_shift; phụ trách nằm trong tổng số người.', 'core/constraints/hard_constraints.py:add_staffing_constraints',E,'5.2'),
    r('A04',H,'Số người tối đa','Số người mỗi ca <= staff_per_shift[ca].max.', 'core/constraints/hard_constraints.py:add_staffing_constraints',E,'5.2'),
    r('A05',H,'Tối đa một ca mỗi ngày ghi lịch','Một ID/ngày chỉ có một trạng thái; tối đa một ca.', 'core/model/model_builder.py:build_model',E,'5.2'),
    r('A06',H,'Quyền đi ca','Ca thuộc allowed_shifts của nhân sự.', 'core/constraints/hard_constraints.py:add_shift_eligibility',E,'5.2'),
    r('A07',H,'Nối tiếp ngày trước','Mỗi ca có đủ min_previous_day_staff người đã trực bất kỳ ca 1/2/3 ngày trước; ngày 1 dùng Previous.', 'core/constraints/hard_constraints.py:add_previous_day_continuity',E,'5.2'),
    r('B01',H,'Chuỗi ca đêm','Không vượt max_consecutive_night_shifts, kể cả nối tháng.', 'core/constraints/hard_constraints.py:add_consecutive_shift_constraints;core/validation/sequence_checks.py:check_sequences',E,'5.2'),
    r('B02',H,'Cặp ca cấm','Không có cặp ca liền ngày thuộc forbidden_transitions (bắt buộc có 3->1).', 'core/validation/transition_checks.py:transition_config;core/validation/transition_checks.py:check_transitions',E,'5.3'),
    r('B03',H,'Chuỗi ngày trực','Không vượt max_consecutive_shift_days; chỉ ca 1/2/3 tính vào chuỗi, không tính T/CT/DT/BKD.', 'core/validation/sequence_checks.py:check_sequences',E,'5.2'),
    r('B04',H,'Nghỉ giữa hai ca trực','Tính bằng datetime từ shift_hours, kể cả qua OFF và biên tháng; >= min_rest_hours; không thay thế kiểm tra giờ phi ca.', 'core/validation/transition_checks.py:check_transitions;core/constraints/hard_constraints.py:add_shift_transition_constraints',E,'5.3'),
    r('B05',H,'Kiểm tra nối tháng','Áp dụng A07, B01/B02/B03/B04 qua Previous; không xét vi phạm chỉ nằm trong quá khứ.', 'core/validation/sequence_checks.py:history_codes;core/validation/transition_checks.py:check_transitions',E,'5.3'),
    r('B_WORK_TIME',H,'Giờ làm và nghỉ với CT/DT/T/BKD','BKD không làm việc; DT/CT/T có thể nối ca 1 ngày ghi lịch kế tiếp. Giờ thực tế và ngưỡng nghỉ phi ca chỉ kiểm tra khi có chính sách; thiếu dữ liệu thì NOT_CHECKED.', 'core/validation/transition_checks.py:check_transitions',M,'5.3'),
    r('C_DEMAND',H,'Nhu cầu ca chính xác','DEMAND_BASED: số người = nhu cầu ngày/ca, mặc định min nếu không khai báo; không dùng preferred để tăng người.', 'core/constraints/credit_constraints.py:add_demand_constraints;core/validation/credit_checks.py:demand_requirements',E,'5.2'),
    r('C_REQUEST_NONSHIFT',H,'Yêu cầu OFF/CT/DT/BKD','Mã lịch thực tế phải đúng mã đăng ký cứng; không trực ca cùng ngày. Không tự đổi mã phi ca khi kiểm tra lịch Excel.', 'core/constraints/admin_constraints.py:add_all_request_constraints;core/validation/admin_checks.py:check_admin_credits',P,'5.2',legacy_ids='C06/C07/C09 (source ghi gộp)'),
    r('C08',H,'Ca cố định','Đúng ca đã đăng ký trong Requests.', 'core/constraints/admin_constraints.py:add_all_request_constraints;core/validation/admin_checks.py:check_admin_credits',E,'5.2'),
    r('C_CODES',H,'Mã phi ca hợp lệ','SHIFT không có T; CT/DT/BKD chỉ theo Requests; OFF mặc định nếu SHIFT không trực và không có yêu cầu. ADMIN theo D_T.', 'core/validation/admin_checks.py:check_admin_credits',P,'5.2'),
    r('C_CREDIT',H,'Quy đổi công','Ca 1/2/3, CT, DT, T = 1; OFF/BKD = 0; công tính từ mã thực tế.', 'core/validation/credit_checks.py:check_work_credit;core/validation/admin_checks.py:AdminCreditRow',E,'5.2'),
    r('C_TARGET',I,'Công chuẩn và thiếu/vượt công','Công chuẩn = ngày thường không lễ hoặc target_credit cá nhân; thiếu công chỉ thống kê, vượt công cảnh báo cần xem xét, không tự coi vô nghiệm.', 'core/data/calendar_utils.py:standard_credit;core/validation/credit_checks.py:target_credit;core/reporting/schedule_report.py:print_report',E,'5.4'),
    r('D_ALLOWED',H,'Điều kiện huy động ADMIN','enabled; ca 2/3 thuộc cả hai quyền; không phụ trách, không trực T7/CN; ngày lễ trong tuần chỉ khi allow_holiday.', 'core/validation/admin_checks.py:admin_allowed;core/data/data_loader.py:load_data;core/constraints/admin_constraints.py:add_admin_constraints',E,'5.2',legacy_ids='D01/D03/D04/D05 (source ghi gộp)'),
    r('D06',H,'Giới hạn ca ADMIN','Tổng số ca kể cả cố định <= max_shifts_per_person nếu không null.', 'core/constraints/admin_constraints.py:add_admin_constraints;core/validation/admin_checks.py:check_admin_credits',E,'5.2'),
    r('D_T',H,'Mã hành chính T/OFF','ADMIN không trực/không có Requests: T ngày thường không lễ, OFF ngày nghỉ/lễ; ca thay T; Requests phi ca được giữ nguyên.', 'core/constraints/admin_constraints.py:add_workforce_objective;core/validation/admin_checks.py:check_admin_credits',P,'5.2'),
    r('STAGE3',O,'Công và huy động','Lex: tổng vượt SHIFT, tổng ca ADMIN, gap công SHIFT, tổng |n*công - tổng công| SHIFT, tổng |a*ca hỗ trợ - tổng hỗ trợ| ADMIN.', 'core/constraints/admin_constraints.py:add_workforce_objective;core/objectives/quality_checks.py:workforce_score',E,'5.4'),
    r('E01',O,'Cân bằng loại ca','SHIFT: max và tổng gap giữa các loại ca được phép của từng người.', 'core/objectives/quality_checks.py:shift_balance_score',E,'5.4','balance_shift_types'),
    r('E02',O,'Cân bằng ca đêm','SHIFT có quyền ca 1; nhóm cùng quyền ca và công chuẩn; max và tổng gap ca đêm.', 'core/objectives/night_checks.py:night_score',E,'5.4','balance_night_shifts'),
    r('E03a',O,'Cân bằng trực lễ','SHIFT: gap và tổng lệch nguyên số lượt trực ngày lễ theo nhãn ngày.', 'core/objectives/day_balance_v2.py:day_score_v2',E,'5.4','balance_holiday_shifts'),
    r('E03b',O,'Cân bằng cuối tuần','SHIFT: gap và tổng lệch nguyên số lượt trực T7/CN không trùng lễ.', 'core/objectives/day_balance_v2.py:day_score_v2;core/objectives/day_balance_checks.py:day_sets',E,'5.4','balance_weekend_shifts'),
    r('E04a',O,'Cụm nghỉ','SHIFT: tổng OFF lẻ, max OFF lẻ/người, số OFF dư sau 3; không coi CT/DT/BKD là OFF; không suy diễn biên chưa biết.', 'core/objectives/rest_checks.py:rest_scores;core/objectives/rest_checks.py:rest_windows',E,'5.4','balance_rest_days'),
    r('E04b',O,'Cuối tuần nghỉ trọn','SHIFT: số người chưa có cuối tuần trọn, tổng cặp người/cuối tuần không trọn, gap cuối tuần trọn, gap đợt OFF >=2. Xét giờ ca; kỳ thiếu dữ liệu báo chờ.', 'core/objectives/rest_checks.py:rest_scores;core/objectives/rest_checks.py:rest_windows',E,'5.4','balance_rest_days'),
    r('E05',O,'Luân phiên ca linh hoạt','SHIFT: nghỉ ngắn, dư chuỗi cùng loại >2, dư cùng loại >3 trong cửa sổ 7 ngày, max bất lợi/người; tổng chuyển chỉ thống kê.', 'core/objectives/flexible_rotation.py:flexible_score;core/objectives/rotation_checks.py:preferred_rest_minutes',E,'5.4','minimize_shift_changes'),
    r('E06',O,'Nguyện vọng mềm','Tổng trọng số không đáp ứng; PREFER_OFF chỉ OFF; PREFER_WORK chỉ ca 1/2/3; PREFER_SHIFT/AVOID_SHIFT xét ca chỉ định.', 'core/objectives/preference_checks.py:preference_score;core/objectives/preference_checks.py:satisfied',E,'5.4','honor_preferences'),
    r('CFG_PREFERRED',I,'Số người preferred','Có trường preferred nhưng chỉ kiểm tra giới hạn; prefer_staffing chưa điều khiển mục tiêu riêng.', 'core/constraints/hard_constraints.py:add_staffing_constraints;core/engine/engine_config.py:STAGES',M,'sau 5.6','prefer_staffing'),
    r('CFG_ADMIN_FLAGS',I,'Công tắc tối ưu ADMIN','minimize_admin_support và balance_admin_support chưa điều khiển độc lập; Stage 3 luôn có hai mục tiêu ADMIN.', 'core/constraints/admin_constraints.py:add_workforce_objective;core/engine/engine_config.py:STAGES',P,'sau 5.6'),
    r('REPAIR',I,'Hạn chế đổi lịch','minimize_schedule_changes chưa thực thi; Warm Start không bảo đảm ít ô đổi. Thuộc chế độ REPAIR sau này.', 'core/engine/engine_config.py:STAGES',M,'7','minimize_schedule_changes'),
)
BY_ID={x.id:x for x in RULES}
if len(BY_ID)!=len(RULES): raise RuntimeError('Trùng mã rule.')
