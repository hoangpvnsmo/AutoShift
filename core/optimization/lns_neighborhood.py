"""LNS neighborhoods, no OR-Tools dependency. Coordinates are (employee, date)."""
from dataclasses import dataclass, asdict
from datetime import timedelta
from pathlib import Path
import hashlib
import random
from core.engine.checkpoint import digest, SCHEMA

NEW_CORE = {'lns_neighborhood.py', 'objective_lns.py', 'objective_resume_lns.py'}


def legacy_fingerprints(data, prefs, root):
    """Exact 4.7.2 fingerprint without the three additive search-only modules.
    Use only to migrate an exact match, never to ignore changes in old source.
    """
    root=Path(root)
    sources={str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.glob('core/*.py')) if p.name not in NEW_CORE}
    for name in ('run_stage3.py','run_step47.py'):
        p=root/name
        if p.exists(): sources[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    base={'month':[data.year,data.month], 'employees':[asdict(e) for e in sorted(data.employees,key=lambda e:e.id)],
          'requests':[[r.employee_id,r.day.isoformat(),r.code] for r in sorted(data.requests,key=lambda r:(r.employee_id,r.day))],
          'holidays':sorted(d.isoformat() for d in data.holidays),
          'previous':[[i,d.isoformat(),v] for (i,d),v in sorted(data.previous.items())],
          'rules':data.rules,'sources':sources,'schema':SCHEMA}
    return digest(base),digest([[p.employee_id,p.day.isoformat(),p.kind,p.shift,p.weight] for p in sorted(prefs,key=lambda p:(p.employee_id,p.day))])


@dataclass(frozen=True)
class Neighborhood:
    kind: str
    cells: frozenset


def make_rng(assignments, seed=0):
    rows=[[i,d.isoformat(),s] for (i,d),s in sorted(assignments.items())]
    return random.Random(int(digest([seed,rows])[:16],16))


def rotation_targets(data, days, a):
    """Rank shift-change locations, weighting short gaps in the actual model.
    A change on first day can involve previous-month history.
    """
    from core.validation.transition_checks import transition_config, gap_minutes
    from core.objectives.rotation_checks import preferred_rest_minutes
    cfg=transition_config(data); threshold=preferred_rest_minutes(data)
    targets=[]
    for e in data.employees:
        if e.group!='SHIFT': continue
        raw=data.previous.get((e.id,days[0]-timedelta(days=1)), 'OFF')
        prev=int(raw) if str(raw) in ('1','2','3') else 0
        for d in days:
            cur=a[e.id,d]
            if prev and cur and prev!=cur:
                targets.append((e.id,d,2 if gap_minutes(cfg,prev,cur)<threshold else 1))
            prev=cur
    return targets


def choose_neighborhood(ids, days, targets, iteration, rng):
    """Cycle targeted / time / staff; widen after unsuccessful rounds.
    Targets are (id,date,positive_weight). All indices are in current month.
    """
    ids=tuple(ids); days=tuple(days)
    if not ids or not days: return Neighborhood('empty',frozenset())
    targets=[(i,d,w) for i,d,w in targets if i in ids and d in days and w>0]
    target=rng.choices(targets,weights=[x[2] for x in targets],k=1)[0] if targets else None
    mode=iteration%3; scale=iteration//3
    if mode==0 and target:
        i,d,_=target
        width=min(len(days),9+2*scale)
        p=days.index(d); start=max(0,min(p-width//2,len(days)-width))
        # First focus on all staff around unmet request/change; later vary staff.
        pool=list(ids); pool.remove(i)
        k=min(len(ids), max(8, len(ids)//2)+2*scale)
        chosen=ids if scale%2==0 else (i,*rng.sample(pool,k-1))
        return Neighborhood('target',frozenset((e,x) for e in chosen for x in days[start:start+width]))
    if mode==2:
        k=min(len(ids),6+2*scale)
        anchor=target[0] if target else rng.choice(ids)
        chosen=(anchor,*rng.sample([i for i in ids if i!=anchor],k-1))
        return Neighborhood('staff',frozenset((i,d) for i in chosen for d in days))
    width=min(len(days),8+2*scale)
    start=rng.randrange(len(days)-width+1)
    return Neighborhood('time',frozenset((i,d) for i in ids for d in days[start:start+width]))
