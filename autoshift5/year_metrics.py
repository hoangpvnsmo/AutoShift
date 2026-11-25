"""Independent annual counts and exposure-adjusted fairness. No Solver imports."""
from collections import Counter, defaultdict
from datetime import timedelta
from fractions import Fraction
from functools import reduce
from math import gcd
from core.data.calendar_utils import month_days, standard_credit
from core.validation.credit_checks import request_map, target_credit
from core.validation.transition_checks import transition_config

ONE=timedelta(days=1)
SHIFT={'1','2','3'}


def weighted_deviation(counts,weights):
    total=sum(counts.get(i,0) for i in weights);weight=sum(weights.values())
    values=[abs(weight*counts.get(i,0)-w*total) for i,w in weights.items()]
    return max(values,default=0),sum(values)


def normalized(weights):
    divisor=reduce(gcd,weights.values(),0) or 1
    return {i:w//divisor for i,w in weights.items()}


def weekend_records(data,known):
    """Count a weekend in the month of Sunday, including next Monday's night."""
    cfg=transition_config(data)
    for sun in month_days(data.year,data.month):
        if sun.weekday()!=6:continue
        sat=sun-ONE;pairs=[];required={sat,sun}
        for delta in range(-1,3):
            day=sat+delta*ONE
            for shift,(start,end) in cfg.intervals.items():
                if delta*1440+start<2880 and delta*1440+end>0:
                    pairs.append((day,str(shift)));required.add(day)
        yield sat,sun,tuple(pairs),required


class YearMetrics:
    def __init__(self,frames,frontier=None):
        self.frames=frames;self.frontier=frontier
        self.original={k:c for f in frames for k,c in f.baseline.items()}
        self.extra={k:c for f in reversed(frames) for k,c in f.data.previous.items()}
        if frontier:self.extra.update(frontier.baseline)
        self.day_frame={d:j for j,f in enumerate(frames) for d in month_days(f.data.year,f.data.month)}
        self.days=sorted(self.day_frame)
        self.people={e.id:e for f in frames for e in f.data.employees}
        self.ids=sorted({e.id for f in frames for e in f.data.employees if e.group=='SHIFT'})
        self.person={(e.id,j):e for j,f in enumerate(frames) for e in f.data.employees}
        self.requests={k:c for f in frames for k,c in request_map(f.data).items()}
        self.weights={'total_shifts':defaultdict(int),'weekend_shifts':defaultdict(int),'full_weekends':defaultdict(int)}
        self.events=[];self.events_for=defaultdict(list);self.pending=[]
        known={**self.extra,**self.original}
        for j,f in enumerate(frames):
            data=f.data;days=month_days(data.year,data.month);req=request_map(data)
            standard=standard_credit(data)
            for e in data.employees:
                if e.group!='SHIFT':continue
                ratio=Fraction(target_credit(data,e),standard) if standard else Fraction(1)
                eligible=[d for d in days if req.get((e.id,d)) not in ('OFF','CT','DT','BKD')]
                self.weights['total_shifts'][e.id]+=round(1000*len(eligible)*ratio)
                self.weights['weekend_shifts'][e.id]+=round(1000*sum(d.weekday()>=5 for d in eligible)*ratio)
            for sat,sun,pairs,required in weekend_records(data,known):
                for e in data.employees:
                    if e.group!='SHIFT':continue
                    i=e.id
                    if not all((i,d) in known for d in required):
                        self.pending.append(dict(employee_id=i,saturday=str(sat),sunday=str(sun)));continue
                    event=(i,sat,sun,pairs);index=len(self.events);self.events.append(event)
                    for day in required:self.events_for[i,day].append(index)
                    if (self.requests.get((i,sat),'OFF')=='OFF' and self.requests.get((i,sun),'OFF')=='OFF'
                            and all(self.requests.get((i,d))!=shift for d,shift in pairs)):
                        self.weights['full_weekends'][i]+=1
        self.weights={name:normalized({i:values.get(i,0) for i in self.ids}) for name,values in self.weights.items()}
        self.full_ceiling=max((sum(self.weights['full_weekends'].values())*sum(event[0]==i for event in self.events)//w
            for i,w in self.weights['full_weekends'].items() if w),default=0)
        self.full_floor=sum(self.full(j,self.original) for j in range(len(self.events)))

    def full(self,index,codes,overlay=None):
        overlay=overlay or {};i,sat,sun,pairs=self.events[index]
        def cell(day):return overlay.get((i,day),codes.get((i,day),self.extra.get((i,day))))
        return int(cell(sat)=='OFF' and cell(sun)=='OFF' and all(cell(d)!=shift for d,shift in pairs))

    def counts(self,codes):
        rows={i:dict(total_shifts=0,weekend_shifts=0,full_weekends=0,work=0) for i in self.ids}
        monthly={};excess=0;admin=0
        for j,f in enumerate(self.frames):
            data=f.data;days=month_days(data.year,data.month)
            for e in data.employees:
                values=[codes[e.id,d] for d in days]
                work=sum(data.rules['work_credit'][c] for c in values);monthly[e.id,j]=work
                if e.group=='SHIFT':
                    row=rows[e.id];row['work']+=work;row['total_shifts']+=sum(c in SHIFT for c in values)
                    row['weekend_shifts']+=sum(codes[e.id,d] in SHIFT for d in days if d.weekday()>=5)
                    excess+=max(0,work-target_credit(data,e))
                else:admin+=sum(c in SHIFT for c in values)
        full=[self.full(j,codes) for j in range(len(self.events))]
        for (i,*_),value in zip(self.events,full):rows[i]['full_weekends']+=value
        return rows,monthly,full,(excess,admin)

    def score(self,rows,monthly,resource,changed=0):
        out=list(resource)
        for name in ('total_shifts','weekend_shifts'):
            out.extend(weighted_deviation({i:r[name] for i,r in rows.items()},self.weights[name]))
        name='full_weekends';weights=self.weights[name];W=sum(weights.values())
        minimum=min((W*rows[i][name]//w for i,w in weights.items() if w),default=0)
        out.append(self.full_ceiling-minimum)
        out.extend(weighted_deviation({i:r[name] for i,r in rows.items()},weights))
        out.append(-sum(r[name] for r in rows.values()))
        spreads=[]
        for j,f in enumerate(self.frames):
            values=[monthly[e.id,j] for e in f.data.employees if e.group=='SHIFT']
            spreads.append(max(values,default=0)-min(values,default=0))
        return tuple(out+[sum(spreads),changed])

    def report(self,codes):
        rows,monthly,full,resource=self.counts(codes)
        changed=sum(codes[k]!=c for k,c in self.original.items())
        employees=[]
        for i,row in rows.items():
            entry=dict(employee_id=i,name=self.people[i].name,**row,targets={})
            for name in self.weights:
                weights=self.weights[name];W=sum(weights.values());total=sum(r[name] for r in rows.values())
                numerator=total*weights.get(i,0)
                entry['targets'][name]=dict(lower=numerator//W if W else 0,upper=(numerator+W-1)//W if W else 0,
                    exposure_weight=weights.get(i,0))
            employees.append(entry)
        ranges={name:dict(min=min((r[name] for r in rows.values()),default=0),max=max((r[name] for r in rows.values()),default=0),
            gap=max((r[name] for r in rows.values()),default=0)-min((r[name] for r in rows.values()),default=0))
            for name in ('total_shifts','weekend_shifts','full_weekends')}
        return dict(employees=employees,ranges=ranges,resource=dict(shift_overtime=resource[0],admin_support=resource[1]),
            score=list(self.score(rows,monthly,resource,changed)),changed_cells=changed,
            counted_weekends=len({sun for i,sat,sun,pairs in self.events}),pending_weekends=self.pending,
            policy='Ca trực 1/2/3; T7/CN kể cả lễ; nghỉ trọn theo giờ thực và ghép biên. Mục tiêu theo ngày khả dụng và tỷ lệ công chuẩn; không tách phụ trách khỏi nhóm trực.')
