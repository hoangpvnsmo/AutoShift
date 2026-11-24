"""5.4: score actual roster codes, without CP-SAT or reconstructing non-shift codes."""
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from core.data.calendar_utils import month_days, standard_credit
from core.validation.transition_checks import transition_config
from core.objectives.rest_checks import rest_windows
from core.objectives.rotation_checks import preferred_rest_minutes
from .working_time import validate_working_time
from .catalog import BY_ID
from .result import Finding, RuleResult, ValidationReport, Severity, Status

OBJECTIVE_IDS=('STAGE3','E01','E02','E03a','E03b','E04a','E04b','E05','E06')
SHIFT={'1','2','3'}
ONE=timedelta(days=1)

def gap(v):
    v=tuple(v); return max(v,default=0)-min(v,default=0)

def deviation(v):
    v=tuple(v); return sum(abs(len(v)*x-sum(v)) for x in v)

def measured(key,score,note,findings=()):
    return RuleResult(key,Status.MEASURED,note,tuple(findings),tuple(score))

def warn(key,message,i=None,d=None,expected=None,actual=None):
    return Finding(key,Severity.WARNING,message,i,d,expected=expected,actual=actual)

def score_vector(report):
    """Return None rather than manufacturing scores for incomplete layers."""
    by={r.rule_id:r for r in report.results}
    return tuple(by[k].score for k in OBJECTIVE_IDS) if all(by[k].status==Status.MEASURED for k in OBJECTIVE_IDS) else None

def validate_quality(data,cells,*,prefs=(),source='codes',work_policy=None,expected_scores=None):
    """Superset of 5.3. Scores may be measured on hard-invalid but structurally valid rosters.
    Reference scores are only compared numerically, never used as a proof.
    """
    if expected_scores is not None:
        expected_scores=tuple(tuple(x) for x in expected_scores)
        if len(expected_scores)!=9 or any(len(x)!=n or any(type(v) is not int or v<0 for v in x) for x,n in zip(expected_scores,(5,2,2,2,2,3,4,4,1))):
            raise ValueError("Vector điểm tham chiếu không hợp lệ.")
    cells=cells if isinstance(cells,Mapping) else tuple(cells); prefs=tuple(prefs)
    base=validate_working_time(data,cells,prefs=prefs,source=source,work_policy=work_policy)
    done={r.rule_id:r for r in base.results}
    if any(done[k].status!=Status.PASS for k in ('INPUT_GRID','INPUT_CONFIG')): return base
    codes=dict(cells) if isinstance(cells,Mapping) else {(i,d):c for i,d,c in cells}
    days=month_days(data.year,data.month); first=days[0]; current=set(days)
    shift=[e for e in data.employees if e.group=='SHIFT']; admin=[e for e in data.employees if e.group=='ADMIN']
    counts={e.id:Counter(codes[e.id,d] for d in days) for e in data.employees}
    target={e.id:standard_credit(data) if e.target_credit is None else e.target_credit for e in data.employees}
    total={i:sum(n*data.rules['work_credit'][c] for c,n in row.items()) for i,row in counts.items()}
    missing={i:max(0,target[i]-v) for i,v in total.items()}; excess={i:max(0,v-target[i]) for i,v in total.items()}
    st=[total[e.id] for e in shift]; supports=[sum(counts[e.id][s] for s in SHIFT) for e in admin]
    def sum_for(values,people): return sum(values[e.id] for e in people)
    done['C_TARGET']=measured('C_TARGET',(sum_for(missing,shift),sum_for(excess,shift),sum_for(missing,admin),sum_for(excess,admin)),
        'Thiếu SHIFT, vượt SHIFT, thiếu ADMIN, vượt ADMIN; thiếu công chỉ thống kê.',
        [warn('C_TARGET','Công vượt chuẩn; cần xem xét.',i,expected=str(target[i]),actual=str(total[i])) for i in total if excess[i]])
    done['STAGE3']=measured('STAGE3',(sum_for(excess,shift),sum(supports),gap(st),deviation(st),deviation(supports)),
        'Vượt SHIFT, hỗ trợ ADMIN, gap công SHIFT, lệch công SHIFT, lệch hỗ trợ ADMIN.')
    g=[gap(counts[e.id][str(s)] for s in set(e.allowed_shifts)) for e in shift]
    done['E01']=measured('E01',(max(g,default=0),sum(g)),'Max gap và tổng gap loại ca được phép.')
    groups=defaultdict(list)
    for e in shift:
        if 1 in e.allowed_shifts: groups[tuple(sorted(set(e.allowed_shifts))),target[e.id]].append(counts[e.id]['1'])
    g=[gap(v) for v in groups.values()]
    done['E02']=measured('E02',(max(g,default=0),sum(g)),f'Ca đêm: {len(groups)} nhóm cùng quyền ca và công chuẩn.')
    for key,ds in [('E03a',[d for d in days if d in data.holidays]),('E03b',[d for d in days if d.weekday()>=5 and d not in data.holidays])]:
        v=[sum(codes[e.id,d] in SHIFT for d in ds) for e in shift]
        done[key]=measured(key,(gap(v),deviation(v)),f'{len(ds)} ngày; {sum(v)} lượt SHIFT; ngày lễ không tính lại vào cuối tuần.')
    if done['INPUT_HISTORY'].status==Status.PASS:
        windows=rest_windows(data,days) # shared definition of evaluable boundary windows, not score function
        singles=[]; extra=[]; blocks=[]; full=[]; changes=[]; short=[]; adjacent=[]; streak=[]; concentration=[]; f04=[]; f04b=[]; f05=[]
        cfg=transition_config(data)
        try: threshold=preferred_rest_minutes(data)
        except ValueError as exc: threshold=None; done['E05']=RuleResult('E05',Status.NOT_CHECKED,str(exc))
        for e in shift:
            i=e.id
            def code(d): return codes[i,d] if d in current else data.previous[i,d]
            def off(d): return code(d)=='OFF'
            isolated=[b for a,b,c in windows.singles if not off(a) and off(b) and not off(c)]
            long=[ds[-1] for ds in windows.long_windows if all(off(d) for d in ds)]
            singles.append(len(isolated)); extra.append(len(long))
            blocks.append(sum(not off(a) and off(b) and off(c) for a,b,c in windows.blocks))
            full.append(sum(off(sat) and off(sun) and all(code(d)!=str(s) for d,s in pairs) for sat,sun,pairs in windows.weekends))
            f04.extend(warn('E04a','OFF đơn lẻ.',i,d) for d in isolated)
            f04.extend(warn('E04a','OFF vượt ngày thứ ba trong cụm.',i,d) for d in long)
            if windows.weekends and full[-1]==0: f04b.append(warn('E04b','Chưa có cuối tuần nghỉ trọn trong các kỳ đủ dữ liệu.',i))
            if threshold is not None:
                n=sh=adj=0
                for d in days:
                    a,b=code(d-ONE),code(d)
                    if a in SHIFT and b in SHIFT:
                        adj+=1
                        if a!=b:
                            n+=1; minutes=1440+cfg.intervals[int(b)][0]-cfg.intervals[int(a)][1]
                            if minutes<threshold:
                                sh+=1; f05.append(warn('E05','Chuyển loại ca có nghỉ dưới ngưỡng mềm.',i,d,expected=f'{threshold/60:g}h',actual=f'{minutes/60:g}h'))
                changes.append(n); short.append(sh); adjacent.append(adj)
                st=co=0
                if len(set(e.allowed_shifts))>1:
                    for d in days:
                        if code(d) in SHIFT and code(d)==code(d-ONE)==code(d-2*ONE): st+=1
                        window=tuple(d-timedelta(days=k) for k in range(7))
                        if all(x in current or (i,x) in data.previous for x in window):
                            counts7=Counter(code(x) for x in window)
                            co+=sum(max(0,counts7[s]-3) for s in SHIFT)
                streak.append(st); concentration.append(co)
        done['E04a']=measured('E04a',(sum(singles),max(singles,default=0),sum(extra)),'OFF thực tế, không tính BKD/CT/DT; chỉ xét cửa sổ có dữ liệu.',f04)
        w=len(windows.weekends)
        done['E04b']=measured('E04b',(sum(x==0 for x in full) if w else 0,len(shift)*w-sum(full),gap(full),gap(blocks)),
            f'{w} cuối tuần đủ dữ liệu; {len(windows.pending_weekends)} kỳ chờ; {sum(full)} lượt nghỉ trọn; đợt OFF >=2: {sum(blocks)}.',f04b)
        if threshold is not None:
            done['E05']=measured('E05',(sum(short),sum(streak),sum(concentration),max((a+b+c for a,b,c in zip(short,streak,concentration)),default=0)),f'{sum(changes)} chuyển ca (chỉ thống kê); {sum(adjacent)} cặp ngày trực liền; ngưỡng mềm {threshold/60:g}h; dư chuỗi >2, dư loại ca >3 trong 7 ngày; max bất lợi/người.',f05)
    else:
        for key in ('E04a','E04b','E05'): done[key]=RuleResult(key,Status.NOT_CHECKED,'Thiếu/sai lịch sử; không suy diễn OFF.')
    bad=[]; penalty=0
    for p in prefs:
        c=codes[p.employee_id,p.day]
        ok={'PREFER_OFF':c=='OFF','PREFER_WORK':c in SHIFT,'PREFER_SHIFT':c==str(p.shift),'AVOID_SHIFT':c!=str(p.shift)}[p.kind]
        if not ok:
            penalty+=p.weight; bad.append(warn('E06',f'Chưa đáp ứng {p.kind}, trọng số {p.weight}.',p.employee_id,p.day,expected=f'{p.kind} {p.shift or ""}'.strip(),actual=c))
    done['E06']=measured('E06',(penalty,),f'Đáp ứng {len(prefs)-len(bad)}/{len(prefs)}; tổng trọng số {sum(p.weight for p in prefs)}.',bad)
    opt=data.rules.get('optimization',{})
    for j,key in enumerate(OBJECTIVE_IDS):
        r=done[key]; flag=BY_ID[key].flag
        if r.status!=Status.MEASURED: continue
        note=r.note; findings=r.findings
        if flag and not opt.get(flag,True): note+=' Tầng tắt trong solver; vẫn tính thống kê.'
        if expected_scores is not None:
            expected=tuple(expected_scores[j])
            if r.score==expected: note+=' Khớp điểm tham chiếu.'
            else:
                note+=' KHÔNG KHỚP điểm tham chiếu; kiểm tra dữ liệu/nguồn lịch.'
                findings+= (warn(key,'Điểm tính lại khác điểm tham chiếu; không xác nhận chứng minh.',expected=str(expected),actual=str(r.score)),)
        if base.errors: note+=' Lịch có lỗi cứng; điểm chỉ để chẩn đoán.'
        done[key]=replace(r,note=note,findings=findings)
    return ValidationReport(data.year,data.month,tuple(done[r.rule_id] for r in base.results),source)
