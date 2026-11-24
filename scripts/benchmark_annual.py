"""Real 12-month CP-SAT benchmark, explicitly declared synthetic inputs.
No fixed current-month requests: all 6,588 employee/date assignments are solved.
"""
from datetime import datetime
from pathlib import Path
from time import perf_counter
import json
import sys
from uuid import uuid4
sys.stderr=sys.stdout
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_v50 import fixture,seed_month
from autoshift5 import models as m
from autoshift5.db import ROOT,make_engine,initialize
from autoshift5.services import Service
from autoshift5.schemas import JobInput
from autoshift5.worker import execute
from autoshift5.annual import confirm_plan,annual_statistics
from autoshift5.reporting import export_annual

folder=ROOT/'artifacts'/('annual-2028-'+datetime.now().strftime('%Y%m%d_%H%M%S')+'-'+uuid4().hex[:8])
folder.mkdir(parents=True,exist_ok=False)
engine=make_engine(folder/'benchmark.db');initialize(engine);service=Service(engine)
with service.sessions.begin() as s:
    plan=m.AnnualPlan(name='2028 synthetic CP-SAT benchmark (free assignments)',year=2028,
        assumptions='18 SHIFT employees, 6 eligible per shift, all leaders. Exactly 3 people per shift/day. Test-only maximum 3 consecutive nights and 5 shift days. Explicit 6-day initial history from a 3-on/3-off rotation. No ADMIN, holidays, preferences or current-month fixed requests. Monthly E01-E06 disabled; Stage 3 and final cumulative fairness run. Real user config remains unchanged.')
    s.add(plan);s.flush();pid=plan.id
    for month in range(1,13):
        data,_=fixture(2028,month,fixed=False)
        seed_month(s,data,plan_id=pid)
start=perf_counter()
job=service.enqueue(JobInput(revision=1,mode='ANNUAL',annual_plan_id=pid,budget=180))
execute(engine,job['id'])
result=service.job(job['id'])
elapsed=perf_counter()-start
report=dict(seconds=round(elapsed,3),budget=180,solver='real OR-Tools CP-SAT',fixed_current_month_requests=0,
            employees=18,days=366,cells=18*366,job=result,assumptions=plan.assumptions,browser_verified=False)
if result['status']=='SUCCEEDED':
    confirm_plan(service,pid,result['result']['version_ids'],1)
    stats=annual_statistics(service,pid)
    report['statistics']=stats
    report['export']=str(export_annual(service,pid,folder))
(folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print(json.dumps(dict(status=result['status'],error=result['error'],seconds=round(elapsed,3),folder=str(folder),
                     versions=len((result.get('result') or {}).get('version_ids',[]))),ensure_ascii=False,indent=2))
engine.dispose()
sys.exit(0 if result['status']=='SUCCEEDED' else 1)
