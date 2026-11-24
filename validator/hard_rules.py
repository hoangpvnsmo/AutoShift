"""5.2: kiểm tra mã lịch thực tế, độc lập CP-SAT; giờ làm để bước 5.3."""
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date, timedelta
from core.data.calendar_utils import month_days
from core.validation.credit_checks import demand_requirements, check_work_credit
from core.validation.admin_checks import validate_admin_requests
from core.validation.sequence_checks import sequence_limits
from core.validation.transition_checks import transition_config
from core.objectives.preference_checks import validate_preferences
from .catalog import RULES
from .result import Finding, RuleResult, ValidationReport, Severity, Status
from .structure import grid_result, history_result

SHIFT = frozenset(('1','2','3'))
NONSHIFT = frozenset(('OFF','CT','DT','BKD'))
CHECK_IDS = ('A01','A02','A03','A04','A05','A06','A07','B01','B03',
             'C_DEMAND','C_REQUEST_NONSHIFT','C08','C_CODES','C_CREDIT','D_ALLOWED','D06','D_T')


def config_result(data, prefs):
    """Kiểm tra dữ liệu có kiểu MonthData; parser Excel kiểm tra các hàng thô trước đó."""
    issues=[]
    def check(label, fn):
        try: return fn()
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError) as exc:
            issues.append(Finding('INPUT_CONFIG',Severity.ERROR,f'{label}: {exc}'))
            return None
    def require(ok, text):
        if not ok: raise ValueError(text)
    def integer(v, lo): return type(v) is int and v>=lo
    def employees():
        seen=set()
        for e in data.employees:
            require(integer(e.id,1) and e.id not in seen, f'ID sai hoặc trùng: {e.id}')
            seen.add(e.id)
            require(isinstance(e.name,str) and bool(e.name.strip()), f'ID {e.id}: thiếu tên')
            require(e.group in ('SHIFT','ADMIN') and type(e.is_leader) is bool, f'ID {e.id}: group/is_leader sai')
            require(isinstance(e.allowed_shifts,(tuple,list)) and all(type(s) is int and s in (1,2,3) for s in e.allowed_shifts) and len(set(e.allowed_shifts))==len(e.allowed_shifts), f'ID {e.id}: quyền ca sai')
            require(e.target_credit is None or integer(e.target_credit,0), f'ID {e.id}: công chuẩn sai')
            require(e.group!='SHIFT' or bool(e.allowed_shifts), f'ID {e.id}: SHIFT cần quyền ca')
            require(e.group!='ADMIN' or (not e.is_leader and 1 not in e.allowed_shifts), f'ID {e.id}: ADMIN không làm phụ trách/ca 1')
        require(any(e.group=='SHIFT' for e in data.employees),'Cần ít nhất một SHIFT')
    def calendar():
        days=set(month_days(data.year,data.month))
        require(all(type(d) is date and d in days for d in data.holidays),'Ngày lễ ngoài tháng hoặc sai kiểu')
    def staffing():
        r=data.rules
        require(type(r['max_shifts_per_day']) is int and r['max_shifts_per_day']==1,'V1 chỉ hỗ trợ một ca/ngày')
        require(set(r['staff_per_shift'])==SHIFT,'Cần cấu hình đúng ba ca')
        for k in ('min_leaders_per_shift','min_previous_day_staff'):
            require(integer(r[k],1),f'{k} cần số nguyên dương')
        for s in SHIFT:
            c=r['staff_per_shift'][s]
            require(integer(c['min'],3) and integer(c['preferred'],c['min']) and integer(c['max'],c['preferred']),f'Ca {s}: min/preferred/max sai')
            require(max(r['min_leaders_per_shift'],r['min_previous_day_staff'])<=c['max'],f'Ca {s}: yêu cầu phụ trách/nối tiếp vượt max')
    check('Employees',employees); check('Month/Holidays',calendar); check('Staffing',staffing)
    check('Chuỗi ca',lambda: sequence_limits(data))
    check('Giờ ca (chỉ kiểm tra cấu hình)',lambda: transition_config(data))
    check('Nhu cầu',lambda: demand_requirements(data))
    check('Quy đổi công',lambda: check_work_credit(data))
    check('ADMIN/Requests',lambda: validate_admin_requests(data))
    check('Preferences',lambda: validate_preferences(data,prefs))
    return RuleResult('INPUT_CONFIG',Status.FAIL if issues else Status.PASS,
        'Kiểm tra cấu hình hiện hành và đầu vào đã chuẩn hóa; không kiểm tra giờ làm phi ca.',tuple(issues))


def validate_hard_rules(data, cells, *, prefs=(), source='codes'):
    """Mã lịch trực tiếp, không tự sửa mã. Iterable được materialize đúng một lần.

    INPUT_GRID/INPUT_CONFIG sai: dừng các check phụ thuộc. INPUT_HISTORY sai:
    vẫn kiểm tra các nhóm không phụ thuộc quá khứ, không mặc định OFF.
    prefs=() nghĩa là không có nguyện vọng; CLI đọc sheet Preferences thật.
    """
    cells=cells if isinstance(cells,Mapping) else tuple(cells)
    grid=grid_result(data,cells); hist=history_result(data); cfg=config_result(data,prefs)
    done={r.rule_id:r for r in (grid,hist,cfg)}
    findings=defaultdict(list)
    def fail(rule,msg,i=None,d=None,s=None,expected=None,actual=None,related=None):
        findings[rule].append(Finding(rule,Severity.ERROR,msg,i,d,s,
            None if expected is None else str(expected),None if actual is None else str(actual),related))
    def finish(rule,note):
        fs=tuple(findings[rule]); done[rule]=RuleResult(rule,Status.FAIL if fs else Status.PASS,note,fs)
    # Duplicate cells represent multiple assignments or ambiguous source, not a repaired mapping.
    for f in grid.findings:
        if f.message.startswith('Trùng ô lịch'):
            fail('A05','Một ID/ngày xuất hiện nhiều lần; không cho phép kiêm nhiệm.',f.employee_id,f.day,expected=1,actual='>1')
    if findings['A05']: finish('A05','Phát hiện trùng ID/ngày trong dữ liệu gốc.')
    if grid.status==Status.PASS and cfg.status==Status.PASS:
        codes=dict(cells) if isinstance(cells,Mapping) else {(i,d):c for i,d,c in cells}
        days=month_days(data.year,data.month); people={e.id:e for e in data.employees}
        reqpolicy,req=validate_admin_requests(data); demands=demand_requirements(data)
        r=data.rules; history_ok=hist.status==Status.PASS
        for d in days:
            prev=d-timedelta(days=1)
            for s in (1,2,3):
                staff=[i for i in people if codes[i,d]==str(s)]; n=len(staff)
                lim=r['staff_per_shift'][str(s)]
                if not n: fail('A01','Không có người trực ca.',d=d,s=s,expected='>=1',actual=n)
                if n<lim['min']: fail('A02','Thiếu người trực ca.',d=d,s=s,expected=lim['min'],actual=n)
                if n>lim['max']: fail('A04','Vượt số người tối đa.',d=d,s=s,expected=lim['max'],actual=n)
                leaders=sum(people[i].is_leader and people[i].group=='SHIFT' for i in staff)
                if leaders<r['min_leaders_per_shift']: fail('A03','Thiếu phụ trách ca.',d=d,s=s,expected=r['min_leaders_per_shift'],actual=leaders)
                if n!=demands[d,s]: fail('C_DEMAND','Không đúng nhu cầu ngày/ca.',d=d,s=s,expected=demands[d,s],actual=n)
                if history_ok:
                    carry=sum((data.previous[i,prev] if prev<days[0] else codes[i,prev]) in SHIFT for i in staff)
                    if carry<r['min_previous_day_staff']: fail('A07','Thiếu người trực nối tiếp từ ngày trước.',d=d,s=s,expected=r['min_previous_day_staff'],actual=carry,related=prev)
        max_night,max_work=sequence_limits(data); credit_totals=Counter()
        for i,e in people.items():
            night=work=0; n_admin=0
            if history_ok:
                for k in range(max(max_night,max_work),0,-1):
                    c=data.previous[i,days[0]-timedelta(days=k)]
                    night=night+1 if c=='1' else 0; work=work+1 if c in SHIFT else 0
            for d in days:
                c=codes[i,d]; fixed=req.get((i,d)); s=int(c) if c in SHIFT else None
                credit_totals[e.group]+=r['work_credit'][c]
                if s is not None and s not in e.allowed_shifts:
                    fail('A06','Ca không thuộc quyền đi ca.',i,d,s,expected=e.allowed_shifts,actual=s)
                if fixed in NONSHIFT and c!=fixed:
                    fail('C_REQUEST_NONSHIFT','Sai yêu cầu bắt buộc phi ca (không kiêm nhiệm trong ngày).',i,d,s,expected=fixed,actual=c)
                if fixed in SHIFT and c!=fixed:
                    fail('C08','Sai ca cố định.',i,d,s,expected=fixed,actual=c)
                if (e.group=='SHIFT' and c=='T') or (c in {'CT','DT','BKD'} and fixed!=c):
                    fail('C_CODES','Mã lịch phi ca không được phép hoặc không có yêu cầu tương ứng.',i,d,expected='mã được phép theo nhóm/Requests',actual=c)
                if e.group=='ADMIN':
                    if s is not None:
                        n_admin+=1
                        if not (reqpolicy.enabled and s in reqpolicy.allowed_shifts and s in e.allowed_shifts and not e.is_leader and d.weekday()<5 and (d not in data.holidays or reqpolicy.allow_holiday)):
                            fail('D_ALLOWED','Ca ADMIN trái chính sách huy động.',i,d,s,expected='ADMIN được bật, ca 2/3 hợp quyền, ngày hợp lệ',actual=c)
                    elif fixed is None:
                        expected='T' if d.weekday()<5 and d not in data.holidays else 'OFF'
                        if c!=expected: fail('D_T','Sai mã T/OFF của ADMIN.',i,d,expected=expected,actual=c)
                if history_ok:
                    night=night+1 if c=='1' else 0; work=work+1 if c in SHIFT else 0
                    if night>max_night: fail('B01','Vượt chuỗi ca đêm liên tiếp.',i,d,s,expected=max_night,actual=night,related=d-timedelta(days=night-1))
                    if work>max_work: fail('B03','Vượt chuỗi ngày trực liên tiếp.',i,d,s,expected=max_work,actual=work,related=d-timedelta(days=work-1))
            if e.group=='ADMIN' and reqpolicy.max_shifts is not None and n_admin>reqpolicy.max_shifts:
                fail('D06','Vượt giới hạn ca ADMIN trong tháng.',i,expected=reqpolicy.max_shifts,actual=n_admin)
        for rule in CHECK_IDS:
            if rule in ('A07','B01','B03') and not history_ok: continue
            if rule=='D06' and reqpolicy.max_shifts is None:
                done[rule]=RuleResult(rule,Status.NOT_APPLICABLE,'Không đặt giới hạn số ca ADMIN (null).'); continue
            note='Đã kiểm tra mã lịch thực tế trên toàn tháng.'
            if rule=='C_CREDIT': note=f"Đã kiểm tra bảng quy đổi V1 và đếm mã: SHIFT={credit_totals['SHIFT']}, ADMIN={credit_totals['ADMIN']} công. Không đối chiếu cột tổng công bên ngoài."
            if rule=='A05': note='Đúng một mã/ID/ngày; ca 1 không kiêm nhiệm CT/DT/T hoặc ca khác cùng ngày ghi lịch.'
            if rule in ('A07','B01','B03'): note='Đã kiểm tra tháng hiện tại và lịch sử trước tháng; không báo lỗi chỉ nằm trong lịch sử.'
            finish(rule,note)
    out=[]
    for spec in RULES:
        if spec.id in done: out.append(done[spec.id]); continue
        if spec.id in CHECK_IDS:
            note='Chưa kiểm tra do cấu trúc/cấu hình không hợp lệ.' if grid.status!=Status.PASS or cfg.status!=Status.PASS else 'Chưa kiểm tra do thiếu/sai lịch sử.'
        elif spec.id=='B_WORK_TIME': note='Chưa có giờ/phân loại CT/DT/T/BKD. Ca 1 không kiêm nhiệm cùng ngày ghi lịch; chưa đánh giá nghỉ giữa các ngày.'
        else: note=f'Chưa thực thi trong 5.2; dự kiến bước {spec.step}.'
        out.append(RuleResult(spec.id,Status.NOT_CHECKED,note))
    return ValidationReport(data.year,data.month,tuple(out),source)
