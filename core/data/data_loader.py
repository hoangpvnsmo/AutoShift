import json
from datetime import date, datetime
from pathlib import Path
from openpyxl import load_workbook
from core.data.models import Employee, Request, MonthData
from core.data.calendar_utils import month_days, previous_days, employee_credit

CODES = {'1','2','3','CT','DT','BKD','OFF','T'}


def as_date(value):
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    return date.fromisoformat(str(value).strip())


def as_int(value):
    if isinstance(value, bool) or value is None: raise ValueError(f'Số nguyên không hợp lệ: {value}')
    n = int(value)
    if isinstance(value, float) and n != value: raise ValueError(f'Số nguyên không hợp lệ: {value}')
    return n


def flag(value):
    n = as_int(value)
    if n not in (0,1): raise ValueError(f'Chỉ chấp nhận 0 hoặc 1: {value}')
    return bool(n)


def code(value):
    return str(value).strip().upper().replace('ĐT','DT')


def rows(wb, sheet, headers):
    values = iter(wb[sheet].values)
    actual = next(values, ())
    if tuple(actual[:len(headers)]) != tuple(headers):
        raise ValueError(f'{sheet}: sai tên hoặc thứ tự cột, cần {headers}')
    for line, row in enumerate(values, 2):
        row = tuple(row[:len(headers)])
        if any(v is not None and v != '' for v in row):
            yield line, row


def load_data(excel_path, rules_path):
    rules = json.loads(Path(rules_path).read_text(encoding='utf-8'))
    with_workbook = load_workbook(excel_path, read_only=True, data_only=True)
    wb = with_workbook
    try:
        month_rows = list(rows(wb, 'Month', ['year','month']))
        if len(month_rows)!=1: raise ValueError('Month: cần đúng một dòng năm/tháng.')
        year, month = map(as_int, month_rows[0][1])
        days = set(month_days(year, month))
        holidays = set()
        for line, (raw, _) in rows(wb,'Holidays',['date','note']):
            d=as_date(raw)
            if d not in days: raise ValueError(f'Holidays dòng {line}: ngoài tháng.')
            if d in holidays: raise ValueError(f'Holidays dòng {line}: trùng ngày.')
            holidays.add(d)
        employees = {}
        for line, (raw_id,name,group,leader,s1,s2,s3,target) in rows(wb,'Employees',['id','name','group','is_leader','shift_1','shift_2','shift_3','target_credit']):
            i=as_int(raw_id); group=str(group).strip().upper(); lead=flag(leader)
            allowed=tuple(s for s,v in enumerate([s1,s2,s3],1) if flag(v))
            target=None if target in (None,'') else as_int(target)
            if i<=0 or i in employees or not name or group not in {'SHIFT','ADMIN'} or (target is not None and target<0):
                raise ValueError(f'Employees dòng {line}: nhân sự không hợp lệ hoặc trùng ID.')
            if group=='ADMIN' and (1 in allowed or lead): raise ValueError(f'Employees dòng {line}: ADMIN không đi ca 1 hoặc làm phụ trách trong V1.')
            if group=='SHIFT' and not allowed: raise ValueError(f'Employees dòng {line}: SHIFT cần ít nhất một loại ca.')
            employees[i]=Employee(i,str(name).strip(),group,lead,allowed,target)
        if not employees: raise ValueError('Employees: thiếu nhân sự.')
        requests = {}; prev={}
        for line,(raw_id,raw_day,raw_code,note) in rows(wb,'Requests',['id','date','code','note']):
            i=as_int(raw_id); d=as_date(raw_day); c=code(raw_code)
            if i not in employees or d not in days or c not in CODES-{'T'}: raise ValueError(f'Requests dòng {line}: ID/ngày/mã không hợp lệ.')
            if (i,d) in requests: raise ValueError(f'Requests dòng {line}: trùng yêu cầu ID {i}, ngày {d}.')
            if c in {'1','2','3'}:
                e=employees[i]
                if int(c) not in e.allowed_shifts: raise ValueError(f'Requests dòng {line}: ca cố định không được phép.')
                if e.group=='ADMIN':
                    a=rules['admin_support']
                    if not a['enabled'] or (d.weekday()>=5 and not a['allow_weekend']) or (d in holidays and not a['allow_holiday']):
                        raise ValueError(f'Requests dòng {line}: ca hành chính trái cấu hình.')
            requests[i,d]=Request(i,d,c,str(note or ''))
        n=max(rules['max_consecutive_night_shifts'],rules['max_consecutive_shift_days'],1)
        history=set(previous_days(year,month,n))
        for line,(raw_id,raw_day,raw_code) in rows(wb,'Previous',['id','date','code']):
            i=as_int(raw_id); d=as_date(raw_day); c=code(raw_code)
            if i not in employees or d not in history or c not in CODES or (i,d) in prev:
                raise ValueError(f'Previous dòng {line}: ID/ngày/mã không hợp lệ hoặc trùng.')
            prev[i,d]=c
        missing={(i,d) for i in employees for d in history}-prev.keys()
        if missing:
            example=min(missing)
            raise ValueError(f'Previous: thiếu {len(missing)} trạng thái, ví dụ ID {example[0]} ngày {example[1]}. OFF phải ghi rõ, không để trống.')
        data=MonthData(year,month,frozenset(holidays),tuple(employees.values()),tuple(requests.values()),prev,rules)
        return data
    finally:
        wb.close()


def basic_warnings(data):
    warnings=[]
    for e in data.employees:
        r=employee_credit(data,e)
        if e.group=='SHIFT' and (r['required_shifts']<0 or r['remaining_shifts']<0 or r['required_shifts']>r['available_days']):
            warnings.append(f"ID {e.id}: không thể đủ công theo giới hạn số ngày/ca cố định.")
    for s, v in data.rules['staff_per_shift'].items():
        if not (3 <= v['min'] <= v['preferred'] <= v['max']):
            warnings.append(f'Ca {s}: cấu hình min/preferred/max không hợp lệ.')
    if data.rules['shift_hours'] is None:
        warnings.append('Chưa khai báo giờ bắt đầu/kết thúc ca; chưa thể kiểm tra nghỉ giữa ca.')
    return warnings
