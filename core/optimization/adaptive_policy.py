"""Search allocation only; no CP-SAT dependency and no business rules."""
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from core.engine.checkpoint import digest, SCHEMA
from core.optimization.lns_neighborhood import NEW_CORE
from dataclasses import asdict
import hashlib

ADAPTIVE_CORE = {'adaptive_policy.py','objective_alns.py','objective_resume_alns.py'}
MODES = ('target','time','staff')


def previous_fingerprints(data,prefs,root,version='473'):
    """Recreate exact historical hash, excluding only known additive modules."""
    root=Path(root)
    excluded=ADAPTIVE_CORE | (NEW_CORE if version=='472' else set())
    sources={str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.glob('core/*.py')) if p.name not in excluded}
    for name in ('run_stage3.py','run_step47.py'):
        p=root/name
        if p.exists(): sources[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    base={'month':[data.year,data.month], 'employees':[asdict(e) for e in sorted(data.employees,key=lambda e:e.id)],
          'requests':[[r.employee_id,r.day.isoformat(),r.code] for r in sorted(data.requests,key=lambda r:(r.employee_id,r.day))],
          'holidays':sorted(d.isoformat() for d in data.holidays),
          'previous':[[i,d.isoformat(),v] for (i,d),v in sorted(data.previous.items())],
          'rules':data.rules,'sources':sources,'schema':SCHEMA}
    return digest(base),digest([[p.employee_id,p.day.isoformat(),p.kind,p.shift,p.weight] for p in sorted(prefs,key=lambda p:(p.employee_id,p.day))])


@dataclass
class OperatorStats:
    attempts: int=0
    improvements: int=0
    seconds: float=0.
    primary_gain: int=0

    def weight(self,slice_time):
        # Smoothed successes per time; bounded contribution from primary gains.
        return (self.improvements+1)/(self.attempts+2) * (1+min(self.primary_gain,3*self.improvements)/(3*max(1,self.improvements))) / max(.5,(self.seconds+slice_time)/(slice_time*(self.attempts+1)))


class AdaptivePolicy:
    def __init__(self,limit,fraction=.5,slice_time=5.,stall_limit=5):
        if not all(isfinite(x) for x in (limit,fraction,slice_time)) or limit<=0 or not 0<fraction<=.9 or slice_time<=0 or stall_limit<3:
            raise ValueError('Ngân sách ALNS không hợp lệ.')
        self.limit=limit; self.slice=slice_time; self.stall_limit=stall_limit
        self.cap=min(.8,fraction); self.initial=self.cap
        self.stats={m:OperatorStats() for m in MODES}
        self.stall=0; self.attempts=0; self.improvements=0

    def select(self,rng,has_targets=True):
        modes=MODES if has_targets else ('time','staff')
        for mode in modes:
            if not self.stats[mode].attempts: return mode
        if rng.random()<.2: return rng.choice(modes)
        return rng.choices(modes,weights=[self.stats[m].weight(self.slice) for m in modes],k=1)[0]

    def iteration(self,mode):
        # Reuse existing neighborhood implementation; widen after two misses.
        scale=min(5,self.stall//2 + self.stats[mode].attempts//4)
        return 3*scale+MODES.index(mode)

    def record(self,mode,before,after,seconds):
        if mode not in self.stats or not isfinite(seconds) or seconds<0 or after>before:
            raise ValueError('Thống kê ALNS không hợp lệ.')
        s=self.stats[mode]; s.attempts+=1; s.seconds+=seconds; self.attempts+=1
        if after<before:
            s.improvements+=1; self.improvements+=1; self.stall=0
            s.primary_gain+=max(0,before[0]-after[0])
            self.cap=min(.8,self.cap+.05)
        else:
            self.stall+=1

    def remaining_local(self,elapsed):
        if self.stall>=self.stall_limit: return 0.
        return max(0.,self.limit*self.cap-elapsed)

    def reason(self,elapsed):
        return 'không cải thiện liên tiếp' if self.stall>=self.stall_limit else 'hết ngân sách cục bộ' if self.remaining_local(elapsed)<=.05 else 'kết thúc vùng tìm kiếm'

    def summary(self):
        return ', '.join(f'{m}={s.improvements}/{s.attempts} cải thiện, {s.seconds:.2f}s' for m,s in self.stats.items())
