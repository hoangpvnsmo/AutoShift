"""Diagnostic only: use an explicitly supplied backup, never the live database."""
from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from autoshift5 import models as m
from autoshift5.db import make_engine, DEFAULT_DB, revision
from autoshift5.services import Service
from autoshift5.schemas import JobInput
from autoshift5.worker import execute

path=Path(sys.argv[1]).resolve()
if path==DEFAULT_DB.resolve() or not path.is_file():raise SystemExit('Supply an existing backup database.')
engine=make_engine(path);service=Service(engine)
with service.sessions() as s:
    root=s.scalar(select(m.PlanningMonth).where(m.PlanningMonth.year==2026,m.PlanningMonth.month==12))
    before={x.id:x.current_version_id for x in s.scalars(select(m.PlanningMonth))}
    rev=revision(s);mid=root.id
job=service.enqueue(JobInput(revision=rev,mode='FRESH',month_id=mid,cascade=True,cascade_months=12,budget=45))
execute(engine,job['id']);j=service.job(job['id'])
with service.sessions() as s:
    assert revision(s)==rev
    assert {x.id:x.current_version_id for x in s.scalars(select(m.PlanningMonth))}==before
r=j['result'] or {}
print(json.dumps(dict(database=str(path),job_id=j['id'],status=j['status'],error=j['error'],
    month_summaries=r.get('month_summaries'),repair=r.get('repair'),proof=r.get('proof'),
    boundaries=[{k:b[k] for k in ('left','right','verdict','errors','hard_pending')} for b in r.get('boundaries',[])],
    attempts=[dict(months=a['months'],status=a['status']) for a in r.get('attempts',[])],
    original_pointers_unchanged=True),ensure_ascii=True,indent=2))
if j['status']!='SUCCEEDED':print(j['log'])
