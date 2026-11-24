"""5.1: kiểm tra cấu trúc, chưa thực thi rule nghiệp vụ A/B/C/D."""
from collections.abc import Mapping
from datetime import date
from core.data.calendar_utils import month_days, previous_days
from .catalog import RULES
from .result import Finding, RuleResult, ValidationReport, Severity, Status

CODES=frozenset({'1','2','3','OFF','CT','DT','BKD','T'})


def grid_result(data, cells):
    """cells: mapping (id,date)->str hoặc iterable (id,date,code), không tự chữa lỗi."""
    expected={(e.id,d) for e in data.employees for d in month_days(data.year,data.month)}
    seen=set(); findings=[]
    def error(message,i=None,d=None,actual=None):
        findings.append(Finding('INPUT_GRID',Severity.ERROR,message,i,d,actual=actual))
    iterable=((k[0],k[1],v) if isinstance(k,tuple) and len(k)==2 else (k,v) for k,v in cells.items()) if isinstance(cells,Mapping) else cells
    for row in iterable:
        if not isinstance(row,(tuple,list)) or len(row)!=3:
            error('Dòng lịch phải có đúng ID, ngày, mã.',actual=repr(row)); continue
        i,d,c=row
        if type(i) is not int or type(d) is not date:
            error('ID phải là int, ngày phải là date.',actual=repr(row)); continue
        key=(i,d)
        if key not in expected:
            error('Ô lịch ngoài danh sách ID/ngày cần kiểm tra.',i,d,repr(c)); continue
        if key in seen: error('Trùng ô lịch ID/ngày.',i,d,repr(c))
        seen.add(key)
        if type(c) is not str or c not in CODES:
            error('Mã lịch không hợp lệ; không mặc định ô trống là OFF.',i,d,repr(c))
    for i,d in sorted(expected-seen): error('Thiếu ô lịch.',i,d)
    return RuleResult('INPUT_GRID',Status.FAIL if findings else Status.PASS,
        f'Đã đối chiếu {len(seen)}/{len(expected)} ô ID/ngày; chỉ kiểm tra cấu trúc.',tuple(findings))


def history_result(data):
    try:
        n1=data.rules['max_consecutive_night_shifts']; n2=data.rules['max_consecutive_shift_days']
        if any(type(n) is not int or n<1 for n in (n1,n2)): raise ValueError('Giới hạn chuỗi phải là số nguyên dương.')
        n=max(n1,n2,1)
    except (KeyError,ValueError,TypeError) as exc:
        return RuleResult('INPUT_HISTORY',Status.NOT_CHECKED,f'Chưa xác định được số ngày lịch sử: {exc}')
    expected={(e.id,d) for e in data.employees for d in previous_days(data.year,data.month,n)}
    findings=[]
    for i,d in sorted(expected):
        key=i,d
        if key not in data.previous:
            findings.append(Finding('INPUT_HISTORY',Severity.ERROR,'Thiếu dữ liệu lịch sử; không suy diễn OFF.',i,d))
        else:
            c=data.previous[key]
            if type(c) is not str or c not in CODES:
                findings.append(Finding('INPUT_HISTORY',Severity.ERROR,'Mã lịch sử không hợp lệ.',i,d,actual=repr(c)))
    return RuleResult('INPUT_HISTORY',Status.FAIL if findings else Status.PASS,
                      f'Kiểm tra {len(expected)} ô lịch sử ({n} ngày/người); chưa kiểm tra vi phạm chuỗi.',tuple(findings))


def validate_structure(data,cells,*,source='codes'):
    done={x.rule_id:x for x in (grid_result(data,cells),history_result(data))}
    results=[]
    for spec in RULES:
        if spec.id in done: results.append(done[spec.id]); continue
        note=f'Chưa thực thi trong Validator 5.1; dự kiến bước {spec.step}.'
        if spec.id=='B_WORK_TIME': note='Chưa có giờ làm/phân loại CT, DT, T, BKD; chưa thể kiểm tra đầy đủ nghỉ ngơi.'
        results.append(RuleResult(spec.id,Status.NOT_CHECKED,note))
    return ValidationReport(data.year,data.month,tuple(results),source)


def checkpoint_cells(data,assignments):
    """Chuyển mã checkpoint 0/1/2/3 sang mã lịch dự kiến theo dữ liệu hiện hành.

    Không dùng hàm này để xác minh lịch Excel đã công bố: mã phi ca trong
    checkpoint không được lưu riêng. Giữ nguyên mọi ca khác 0, kể cả xung đột.
    Không sửa hoặc lưu checkpoint, không xác minh hash/proven_prefix.
    """
    req={(r.employee_id,r.day):r.code for r in data.requests}
    people={e.id:e for e in data.employees}
    for (i,d),s in assignments.items():
        if type(s) is not int or s not in (0,1,2,3):
            yield i,d,s; continue
        if s:
            yield i,d,str(s); continue
        fixed=req.get((i,d))
        if fixed in {'OFF','CT','DT','BKD'}: c=fixed
        elif i in people and people[i].group=='ADMIN' and d.weekday()<5 and d not in data.holidays: c='T'
        else: c='OFF'
        yield i,d,c
