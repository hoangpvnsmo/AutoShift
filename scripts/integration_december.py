"""Exercise the real HTTP API + owned worker against the imported December data.
Creates an explicitly labeled DT test request; preserves all source versions/files.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
import time
from uuid import uuid4
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autoshift5.db import ROOT

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8765'
folder=ROOT/'artifacts'/('december-integration-'+datetime.now().strftime('%Y%m%d_%H%M%S')+'-'+uuid4().hex[:8])
folder.mkdir(parents=True,exist_ok=False)
with httpx.Client(base_url=base,timeout=30) as c:
    def get(url):
        r=c.get(url);r.raise_for_status();return r.json()
    def post(url,raw):
        r=c.post(url,json=raw);r.raise_for_status();return r.json()
    state=get('/api/health')
    months=get('/api/months')
    month=next(m for m in months if m['year']==2026 and m['month']==12)
    calendar=get(f'/api/months/{month["id"]}/calendar')
    original=calendar['version']['id']
    forbidden={(r['employee_id'],r['day']) for r in calendar['requests']}
    shift=next(a for a in calendar['assignments'] if a['code']=='2' and a['day']=='2026-12-15' and (a['employee_id'],a['day']) not in forbidden)
    request=post('/api/requests/batch',dict(revision=state['revision'],employee_ids=[shift['employee_id']],from_date=shift['day'],
        to_date=shift['day'],code='DT',reason='KIỂM THỬ E2E AutoShift 5.0: yêu cầu DT trên ca 2. Dữ liệu minh họa.',status='APPROVED'))
    pending=get(f'/api/months/{month["id"]}/calendar')
    unchanged=next(a for a in pending['assignments'] if a['employee_id']==shift['employee_id'] and a['day']==shift['day'])
    assert unchanged['code']=='2'
    assert any(r['id']==request['requests'][0]['id'] and r['status']=='APPROVED' for r in pending['requests'])
    before=c.get(f'/api/versions/{original}/export');before.raise_for_status();(folder/'before.xlsx').write_bytes(before.content)
    start=time.perf_counter()
    job=post('/api/jobs',dict(revision=request['revision'],mode='REPAIR',month_id=month['id'],baseline_version_id=original,
        request_ids=[r['id'] for r in request['requests']],budget=45,repair_mode='OPTIMAL',as_of='2026-11-25T12:00:00+07:00'))
    while job['status'] in ('QUEUED','RUNNING','CANCELLING'):
        time.sleep(.5)
        job=get('/api/jobs/'+job['id'])
    if job['status']!='SUCCEEDED':
        (folder/'failed-job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8')
        raise RuntimeError(job.get('error') or job['status'])
    version=job['result']['version_ids'][0]
    current=get(f'/api/months/{month["id"]}/calendar')
    assert current['version']['id']==original
    diff=get(f'/api/versions/compare?left={original}&right={version}')
    assert diff['changed_cells']==job['result']['repair']['changed_cells']
    checked=post('/api/versions/'+version+'/validate',{})
    assert checked['errors']==0
    accepted=post('/api/versions/'+version+'/confirm',dict(revision=request['revision'],expected_current_version_id=original,status='PLANNED'))
    after=c.get(f'/api/versions/{version}/export');after.raise_for_status();(folder/'after.xlsx').write_bytes(after.content)
    history=get(f'/api/months/{month["id"]}/calendar')
    assert any(v['id']==original for v in history['versions'])
    report=dict(month='2026-12',source='Workspace workbook; Guide labels requests/history as illustrative',employee_id=shift['employee_id'],
        day=shift['day'],old_code='2',request='DT',overlay_preserves_original=True,job=job,
        changed_cells=diff['changed_cells'],validation=checked,accepted=accepted,original_version=original,new_version=version,
        original_retained=True,seconds=round(time.perf_counter()-start,3),browser_verified=False,
        browser_limit='Computer Use stopped because it could not identify the Windows browser URL safely.')
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('job','validation')},ensure_ascii=False,indent=2))
