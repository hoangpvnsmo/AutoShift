"""Create a reviewable BALANCE draft through the local API; never confirm it."""
import argparse,json,time,urllib.request,sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser();parser.add_argument('--year',type=int,default=2027);parser.add_argument('--budget',type=float,default=60)
args=parser.parse_args();base='http://127.0.0.1:8765/api'
def get(path):return json.load(urllib.request.urlopen(base+path))
state=get('/health');plans=get('/annual-plans');plan=next(p for p in plans if p['year']==args.year)
before=get('/annual-plans/'+str(plan['id']))
pointers={str(m['id']):m['current_version_id'] for m in before['months']}
body=json.dumps(dict(revision=state['revision'],mode='BALANCE',annual_plan_id=plan['id'],budget=args.budget,
    balance_from_month=1,balance_window_months=3)).encode()
request=urllib.request.Request(base+'/jobs',data=body,headers={'Content-Type':'application/json'})
job=json.load(urllib.request.urlopen(request));print('JOB',job['id'],flush=True)
out=Path('artifacts/year-balance-live-review.json');out.write_text(json.dumps({'job_id':job['id'],'revision':state['revision'],'original_pointers':pointers},indent=2),encoding='utf-8')
while True:
    job=get('/jobs/'+job['id'])
    print(job['status'],job['stage'],flush=True)
    if job['status'] not in ('QUEUED','RUNNING','CANCELLING'):break
    time.sleep(5)
current=get('/annual-plans/'+str(plan['id']))
assert get('/health')['revision']==state['revision']
assert {str(m['id']):m['current_version_id'] for m in current['months']}==pointers
result=job.get('result') or {};report=result.get('annual_balance')
raw=dict(job_id=job['id'],status=job['status'],error=job['error'],version_ids=result.get('version_ids',[]),
    balance=report,proof=result.get('proof'),attempts=result.get('attempts'),
    boundaries=[{k:b[k] for k in ('left','right','errors','hard_pending')} for b in result.get('boundaries',[])],
    original_pointers=pointers,input_revision=state['revision'],current_pointers_unchanged=True)
out.write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding='utf-8')
print('RESULT',json.dumps({k:raw[k] for k in ('job_id','status','error','current_pointers_unchanged')},ensure_ascii=False))
if report:print('BEFORE',report['before']['ranges'],'AFTER',report['after']['ranges'])
assert job['status']=='SUCCEEDED',job['error'] or job['log']
assert not result.get('confirmed')
