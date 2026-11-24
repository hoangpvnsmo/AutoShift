"""5.3: independent datetime checks; no CP-SAT or changes to solver rules."""
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, date, time, timedelta
import re
from core.data.calendar_utils import month_days
from core.validation.sequence_checks import sequence_limits
from core.validation.transition_checks import transition_config
from .hard_rules import validate_hard_rules, SHIFT
from .result import Finding, RuleResult, ValidationReport, Status, Severity

PHI = frozenset(('CT','DT','T','BKD'))
VALID = SHIFT | PHI | {'OFF'}
DAY = 1440


def _clock(s):
    if not isinstance(s,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',s):
        raise ValueError('Giờ phải có dạng HH:MM (00:00–23:59).')
    h,m=map(int,s.split(':')); return h*60+m


def _interval(spec, code):
    if spec is None: return () if code=='BKD' else None
    if not isinstance(spec,dict): raise ValueError('Mỗi mã cần null hoặc object.')
    if spec.get('kind')=='rest' and set(spec)=={'kind'}:
        if code!='BKD': raise ValueError(f'{code} là công việc, không được khai báo là nghỉ.')
        return ()
    if code=='BKD': raise ValueError('BKD không làm việc; chỉ chấp nhận kind: rest hoặc null.')
    if spec.get('kind')!='work' or set(spec)!={'kind','start','end'}:
        raise ValueError('Dùng {kind: rest} hoặc {kind: work, start, end}.')
    a,b=_clock(spec['start']),_clock(spec['end'])
    if a==b: raise ValueError('Giờ bắt đầu/kết thúc trùng nhau.')
    return a,b+(DAY if b<a else 0)


def parse_work_policy(data, policy):
    """No default classifications, and a separate explicitly chosen rest threshold.

    Overrides apply to exactly one employee/date/code; an override cannot
    silently match a different code. Work starts on the labelled calendar day.
    """
    if policy is None: return None
    if not isinstance(policy,dict) or set(policy)-{'min_rest_hours','codes','overrides'} or not {'min_rest_hours','codes'}<=set(policy):
        raise ValueError('Chính sách cần min_rest_hours, codes và overrides tùy chọn.')
    h=policy['min_rest_hours']
    if type(h) not in (int,float) or not 0<=h<=24 or int(h*60)!=h*60:
        raise ValueError('min_rest_hours phi ca phải từ 0–24, chính xác đến phút.')
    raw=policy['codes']
    if not isinstance(raw,dict) or set(raw)-PHI: raise ValueError('codes chỉ chứa CT, DT, T, BKD.')
    defaults={c:_interval(raw.get(c),c) for c in PHI}; defaults['BKD']=()
    overrides={}; people={e.id for e in data.employees}
    rows=policy.get('overrides',[])
    if not isinstance(rows,list): raise ValueError('overrides phải là danh sách.')
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'id','date','code','activity'}: raise ValueError('Override cần id,date,code,activity.')
        i=row['id']; c=row['code']; d=date.fromisoformat(row['date'])
        if type(i) is not int or i not in people or c not in PHI: raise ValueError('Override sai ID/mã phi ca.')
        if (i,d) in overrides: raise ValueError('Override trùng ID/ngày.')
        overrides[i,d]=(c,_interval(row['activity'],c))
    return int(h*60),defaults,overrides


def _stamp(d, minutes): return datetime.combine(d,time())+timedelta(minutes=minutes)


def _finding(rule,msg,i=None,d=None,s=None,expected=None,actual=None,related=None):
    return Finding(rule,Severity.ERROR,msg,i,d,s,None if expected is None else str(expected),None if actual is None else str(actual),related)


def _result(rule, findings, note, complete=True):
    return RuleResult(rule,Status.FAIL if findings else Status.PASS if complete else Status.NOT_CHECKED,note,tuple(findings))


def _shift_checks(data,codes,first,days,cfg,history_ok):
    errors={'B02':[],'B04':[]}; shortest=None
    for e in data.employees:
        i=e.id
        for d in days:
            b=codes[i,d]
            if b not in SHIFT: continue
            for k in range(1,cfg.lookback+1):
                prev=d-timedelta(days=k)
                a=data.previous.get((i,prev)) if prev<first else codes[i,prev]
                if type(a) is not str or a not in SHIFT: continue
                pair=int(a),int(b)
                if k==1 and pair in cfg.explicit:
                    errors['B02'].append(_finding('B02',f'Cặp ca {a} → {b} bị cấm.',i,d,int(b), 'không thuộc forbidden_transitions',f'{a} → {b}',prev))
                end=_stamp(prev,cfg.intervals[int(a)][1]); start=_stamp(d,cfg.intervals[int(b)][0])
                gap=int((start-end).total_seconds()/60)
                shortest=gap if shortest is None else min(shortest,gap)
                if gap<cfg.min_minutes:
                    errors['B04'].append(_finding('B04',f'Ca {a} kết thúc {end:%d/%m %H:%M}; ca {b} bắt đầu {start:%d/%m %H:%M}.',i,d,int(b),f'>= {cfg.min_minutes/60:g}h',f'{gap/60:g}h',prev))
    suffix='' if history_ok else ' Lịch sử thiếu/sai: kết quả chưa đầy đủ.'
    return {
        'B02':_result('B02',errors['B02'],'Kiểm tra cặp ca liền ngày theo forbidden_transitions.'+suffix,history_ok),
        'B04':_result('B04',errors['B04'],f'Kiểm tra mọi cặp ca trong cửa sổ có nguy cơ thiếu nghỉ ({cfg.lookback} ngày); ngưỡng {cfg.min_minutes/60:g}h, kể cả qua OFF. Ngắn nhất trong cửa sổ: {"không có cặp" if shortest is None else f"{shortest/60:g}h"}.'+suffix,history_ok),
    }


def _phi_check(data,codes,first,days,cfg,policy,history_ok):
    rule='B_WORK_TIME'; errors=[]
    try: parsed=parse_work_policy(data,policy)
    except (ValueError,TypeError,KeyError,OverflowError,AttributeError) as exc:
        return _result(rule,[_finding(rule,f'Cấu hình giờ phi ca không hợp lệ: {exc}')],'Không sử dụng chính sách giờ phi ca sai.')
    # Conservative window includes all potentially relevant work intervals,
    # even when the classification/time of a nonshift code is unknown.
    h,defaults,overrides=parsed if parsed is not None else (1440,{**dict.fromkeys(PHI),'BKD':()}, {})
    lo=min(0,*(a for a,b in cfg.intervals.values()))
    hi=max(2*DAY-1,*(b for a,b in cfg.intervals.values()))
    lookback=max(1,(h+hi-lo-1)//DAY)
    dates=tuple(first-timedelta(days=k) for k in range(lookback,0,-1))+tuple(days)
    unknown=[]; missing=[]; seen_overrides=set(); phi_count=0
    for e in data.employees:
        i=e.id; records=[]
        for d in dates:
            c=data.previous.get((i,d)) if d<first else codes[i,d]
            if type(c) is not str or c not in VALID:
                missing.append((i,d)); continue
            if (i,d) in overrides:
                seen_overrides.add((i,d)); expected,iv=overrides[i,d]
                if c!=expected:
                    errors.append(_finding(rule,'Override không khớp mã lịch thực tế.',i,d,expected=expected,actual=c)); continue
            elif c in PHI: iv=defaults[c]
            elif c in SHIFT: iv=cfg.intervals[int(c)]
            else: iv=()
            if c in PHI: phi_count+=1
            if iv is None:
                unknown.append((i,d,c)); continue
            if iv: records.append((d,c,_stamp(d,iv[0]),_stamp(d,iv[1])))
        # Compare actual chronological intervals; label dates may differ from start dates.
        records.sort(key=lambda rec:(rec[2],rec[3],rec[0]))
        for j,(d1,c1,a1,b1) in enumerate(records):
            for d2,c2,a2,b2 in records[j+1:]:
                if (a2-b1).total_seconds()/60>=h: break
                if max(d1,d2)<first or not (c1 in PHI or c2 in PHI): continue
                gap=int((a2-b1).total_seconds()/60)
                # h may be 0, but overlaps must always fail.
                if gap<h:
                    day=max(d1,d2); other=min(d1,d2)
                    errors.append(_finding(rule,f'{c1} ({d1:%d/%m}) kết thúc {b1:%d/%m %H:%M}; {c2} ({d2:%d/%m}) bắt đầu {a2:%d/%m %H:%M}. '+('Chồng lấn giờ làm.' if gap<0 else 'Không đủ thời gian nghỉ.'),i,day,expected=f'>= {h/60:g}h, không chồng lấn',actual=f'{gap/60:g}h',related=other))
    for i,d in sorted(set(overrides)-seen_overrides):
        errors.append(_finding(rule,'Override ngoài phạm vi lịch kiểm tra hoặc không có mã lịch.',i,d))
    if not phi_count and not missing and history_ok and not errors:
        return RuleResult(rule,Status.NOT_APPLICABLE,'Không có mã phi ca CT/DT/T/BKD trong phạm vi xét.')
    note=f'Xét giờ phi ca trong tháng và {lookback} ngày trước tháng; không xét lịch tháng sau chưa cung cấp.'
    if parsed is None: note+=' BKD không làm việc. DT/CT/T được phép nối ca đêm theo ngày ghi lịch; chưa kiểm tra giờ thực tế, không tự áp ngưỡng nghỉ 8h cho phi ca.'
    else: note+=f' Ngưỡng nghỉ phi ca do người dùng cấu hình: {h/60:g}h.'
    if unknown:
        note+=f' Thiếu giờ/phân loại {len(unknown)} ô: '+', '.join(f'{c}={sum(x[2]==c for x in unknown)}' for c in sorted({x[2] for x in unknown}))+'.'
    if missing: note+=f' Thiếu/sai {len(missing)} ô lịch sử trong cửa sổ.'
    return _result(rule,errors,note,not unknown and not missing and history_ok)


def validate_working_time(data,cells,*,prefs=(),source='codes',work_policy=None):
    """5.3 superset of 5.2. Does not change 5.2 API or mutate input."""
    cells=cells if isinstance(cells,Mapping) else tuple(cells)
    base=validate_hard_rules(data,cells,prefs=prefs,source=source)
    done={r.rule_id:r for r in base.results}
    if any(done[k].status!=Status.PASS for k in ('INPUT_GRID','INPUT_CONFIG')):
        return base
    codes=dict(cells) if isinstance(cells,Mapping) else {(i,d):c for i,d,c in cells}
    days=month_days(data.year,data.month); first=days[0]; cfg=transition_config(data)
    history_ok=done['INPUT_HISTORY'].status==Status.PASS
    done.update(_shift_checks(data,codes,first,days,cfg,history_ok))
    boundary=[]; scope=('A07','B01','B02','B03','B04')
    for k in scope:
        for f in done[k].findings:
            if f.related_day is not None and f.related_day<first and f.day is not None and f.day>=first:
                boundary.append(replace(f,rule_id='B05',message=f'{k}: {f.message}'))
    done['B05']=_result('B05',boundary,'Kiểm tra biên tháng trước của A07/B01/B02/B03/B04; không kết luận về lịch tháng sau chưa có.',history_ok and all(done[k].status!=Status.NOT_CHECKED for k in scope))
    done['B_WORK_TIME']=_phi_check(data,codes,first,days,cfg,work_policy,history_ok)
    return ValidationReport(data.year,data.month,tuple(done[r.rule_id] for r in base.results),source)
