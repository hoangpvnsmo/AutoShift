"""Persistent incumbent. Pure Python; no solver dependency."""
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
import hashlib
import json
import os
import tempfile

LAYERS=('workforce','e01','e02','holiday','weekend','rest_a','rest_b','rotation','preferences')
LENGTHS=(5,2,2,2,2,3,4,4,1)
SCHEMA=1


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def source_hashes(root):
    """Hash every Python source under core and the active entry point."""
    root=Path(root)
    sources={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(root.glob('core/**/*.py'))}
    for name in ('run_autoshift.py','run_stage3.py','run_step47.py'):
        p=root/name
        if p.exists(): sources[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    return sources


def fingerprints(data,prefs,root):
    sources=source_hashes(root)
    manifest=Path(root)/'release_manifest.json'
    if manifest.exists(): sources['release_manifest.json']=hashlib.sha256(manifest.read_bytes()).hexdigest()
    return fingerprints_from_sources(data,prefs,sources)


def fingerprints_from_sources(data,prefs,sources):
    base={'month':[data.year,data.month], 'employees':[asdict(e) for e in sorted(data.employees,key=lambda e:e.id)],
          'requests':[[r.employee_id,r.day.isoformat(),r.code] for r in sorted(data.requests,key=lambda r:(r.employee_id,r.day))],
          'holidays':sorted(d.isoformat() for d in data.holidays),
          'previous':[[i,d.isoformat(),v] for (i,d),v in sorted(data.previous.items())],
          'rules':data.rules,'sources':sources,'schema':SCHEMA}
    return digest(base),digest([[p.employee_id,p.day.isoformat(),p.kind,p.shift,p.weight] for p in sorted(prefs,key=lambda p:(p.employee_id,p.day))])


def checkpoint_path(root,year,month,support=None,holidays=(),demo_preferences=False):
    scenario={'support':support,'holidays':sorted(set(d.isoformat() for d in (holidays or ()))), 'preferences':bool(demo_preferences)}
    tag='real' if support is None and not holidays and not demo_preferences else 'demo_'+digest(scenario)[:20]
    return Path(root)/'output'/'checkpoints'/f'{year:04d}-{month:02d}_{tag}.json'


@dataclass(frozen=True)
class Checkpoint:
    base_hash: str
    pref_hash: str
    assignments: dict
    scores: tuple
    proven_prefix: int


def payload(cp):
    return {'schema':SCHEMA,'base_hash':cp.base_hash,'pref_hash':cp.pref_hash,
            'assignments':[[i,d.isoformat(),s] for (i,d),s in sorted(cp.assignments.items())],
            'scores':[list(x) for x in cp.scores],'proven_prefix':cp.proven_prefix}


def read_checkpoint(path):
    path=Path(path)
    if not path.exists(): return None
    raw=json.loads(path.read_text(encoding='utf-8'))
    body=raw['payload']
    if body['schema']!=SCHEMA or raw['checksum']!=digest(body): raise ValueError('Checkpoint sai phiên bản/checksum.')
    assignments={}
    for i,d,s in body['assignments']:
        if type(i) is not int or type(s) is not int or s not in (0,1,2,3): raise ValueError('Checkpoint có mã ca/ID sai.')
        key=i,date.fromisoformat(d)
        if key in assignments: raise ValueError('Checkpoint trùng ID/ngày.')
        assignments[key]=s
    scores=tuple(tuple(x) for x in body['scores'])
    if len(scores)!=len(LENGTHS) or any(len(x)!=n or any(type(v) is not int or v<0 for v in x) for x,n in zip(scores,LENGTHS)):
        raise ValueError('Checkpoint sai cấu trúc điểm.')
    prefix=body['proven_prefix']
    if type(prefix) is not int or not 0<=prefix<=len(LAYERS): raise ValueError('Checkpoint sai proven_prefix.')
    return Checkpoint(body['base_hash'],body['pref_hash'],assignments,scores,prefix)


def write_checkpoint(path,cp):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    body=payload(cp); envelope={'payload':body,'checksum':digest(body)}
    # Write same filesystem, fsync, atomic replace; old file survives incomplete write.
    tmp=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix=path.name+'.',suffix='.tmp',delete=False) as f:
            tmp=Path(f.name); json.dump(envelope,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if tmp is not None and tmp.exists(): tmp.unlink()


def reusable_prefix(cp,base_hash,pref_hash,actual):
    """Only a contiguous proven prefix is reusable; no proof across changed hard/earlier objectives."""
    if cp is None or cp.base_hash!=base_hash: return 0
    limit=len(LAYERS) if cp.pref_hash==pref_hash else len(LAYERS)-1
    for j in range(min(cp.proven_prefix,limit)):
        if cp.scores[j]!=actual[j]: return 0
    return min(cp.proven_prefix,limit)


def prefix_length(statuses):
    proven={'OPTIMAL','PROVEN_BOUND','DISABLED','NO_DAYS','NO_PREFERENCES'}
    n=0
    for s in statuses:
        if s not in proven: break
        n+=1
    return n


def active_vector(scores,active):
    return tuple(v for v,on in zip(scores,active) if on)
