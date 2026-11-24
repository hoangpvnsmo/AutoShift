"""E06: đọc nguyện vọng tùy chọn và đánh giá độc lập với solver."""
from dataclasses import dataclass
from openpyxl import load_workbook
from core.data.data_loader import as_date, as_int, rows
from core.data.calendar_utils import month_days
from core.validation.credit_checks import request_map, SHIFT_CODES, NON_SHIFT_CODES
from core.validation.admin_checks import admin_policy, admin_allowed, check_admin_credits

HEADERS = ['employee_id','date','preference','shift','weight']
KINDS = {'PREFER_OFF','PREFER_SHIFT','AVOID_SHIFT','PREFER_WORK'}

@dataclass(frozen=True)
class Preference:
    employee_id: int
    day: object
    kind: str
    shift: int | None = None
    weight: int = 1


def validate_preferences(data, prefs):
    prefs=tuple(prefs)
    ids={e.id for e in data.employees}; days=set(month_days(data.year,data.month)); seen=set()
    for p in prefs:
        key=(p.employee_id,p.day)
        if p.employee_id not in ids or p.day not in days: raise ValueError(f'Preferences: ID/ngày không hợp lệ: {key}')
        if key in seen: raise ValueError(f'Preferences: mỗi ID/ngày chỉ một nguyện vọng: {key}')
        seen.add(key)
        if p.kind not in KINDS: raise ValueError(f'Preferences: loại không hợp lệ: {p.kind}')
        if p.kind in ('PREFER_SHIFT','AVOID_SHIFT'):
            if type(p.shift) is not int or p.shift not in (1,2,3): raise ValueError('Preferences: cần ca 1, 2 hoặc 3.')
        elif p.shift is not None: raise ValueError('Preferences: loại này phải để trống shift.')
        if type(p.weight) is not int or not 1<=p.weight<=10: raise ValueError('Preferences: weight phải là số nguyên 1..10.')
    return tuple(prefs)


def load_preferences(path,data):
    wb=load_workbook(path,read_only=True,data_only=True)
    try:
        if 'Preferences' not in wb.sheetnames: return ()
        prefs=[]
        for line,(i,d,k,s,w) in rows(wb,'Preferences',HEADERS):
            try:
                prefs.append(Preference(as_int(i),as_date(d),str(k).strip().upper(),None if s in (None,'') else as_int(s),1 if w in (None,'') else as_int(w)))
            except (ValueError,TypeError,OverflowError) as exc: raise ValueError(f'Preferences dòng {line}: {exc}') from exc
        return validate_preferences(data,prefs)
    finally: wb.close()


def satisfied(p,code):
    if p.kind=='PREFER_OFF': return code=='OFF'
    if p.kind=='PREFER_WORK': return code in SHIFT_CODES
    if p.kind=='PREFER_SHIFT': return code==str(p.shift)
    if p.kind=='AVOID_SHIFT': return code!=str(p.shift)
    raise ValueError(f'Loại nguyện vọng không hợp lệ: {p.kind}')


def no_shift_code(e,d,data,fixed):
    if fixed in NON_SHIFT_CODES: return fixed
    if e.group=='ADMIN' and d.weekday()<5 and d not in data.holidays: return 'T'
    return 'OFF'


def possible_codes(data,p,people,req,policy):
    e=people[p.employee_id]; d=p.day; fixed=req.get((e.id,d))
    if fixed is not None: return {fixed}
    codes={no_shift_code(e,d,data,fixed)}
    codes.update(str(s) for s in e.allowed_shifts if e.group=='SHIFT' or admin_allowed(e,d,s,data,policy))
    return codes


def preference_lower(data,prefs):
    people={e.id:e for e in data.employees}; req=request_map(data); policy=admin_policy(data)
    return (sum(p.weight for p in prefs if not any(satisfied(p,c) for c in possible_codes(data,p,people,req,policy))),)


def preference_rows(data,days,a,prefs):
    errors,codes,_=check_admin_credits(data,days,a)
    if errors: raise RuntimeError('\n'.join(errors))
    people={e.id:e for e in data.employees}; req=request_map(data); policy=admin_policy(data)
    out=[]
    for p in prefs:
        c=codes[p.employee_id,p.day]; ok=satisfied(p,c)
        impossible=not any(satisfied(p,x) for x in possible_codes(data,p,people,req,policy))
        out.append((p,c,ok,impossible))
    return out


def preference_score(items):
    return (sum(p.weight for p,c,ok,impossible in items if not ok),)


def demo_preferences(data):
    people=[e for e in data.employees if e.group=='SHIFT']; days=month_days(data.year,data.month); req=request_map(data)
    out=[]; kinds=('PREFER_OFF','PREFER_SHIFT','AVOID_SHIFT','PREFER_WORK')
    for j,k in enumerate(kinds):
        if not people: break
        e=people[j%len(people)]
        d=next((d for d in days[j:] if (e.id,d) not in req and all((p.employee_id,p.day)!=(e.id,d) for p in out)),None)
        if d is not None: out.append(Preference(e.id,d,k,e.allowed_shifts[0] if k in ('PREFER_SHIFT','AVOID_SHIFT') else None))
    return validate_preferences(data,out)
