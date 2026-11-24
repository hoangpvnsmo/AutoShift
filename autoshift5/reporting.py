"""Preserve the existing six-sheet exporter and add version/repair metadata."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from io import BytesIO
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Border, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from core.reporting.excel_report import write_full_report, header, body, widths
from validator.quality import validate_quality
from . import models as m
from .domain import deserialize
from .repository import require, codes_for, validation_for


def export_version(service, version_id, directory):
    with service.sessions() as s:
        v=require(s,m.ScheduleVersion,version_id)
        snap=require(s,m.InputSnapshot,v.snapshot_id)
        data,prefs=deserialize(snap.payload)
        codes=codes_for(s,v.id)
        report=validate_quality(data,codes,prefs=prefs,work_policy=snap.work_policy)
        changes=s.scalars(select(m.ScheduleChange).where(m.ScheduleChange.version_id==v.id)).all()
        stored=validation_for(s,v.id)
        meta=dict(version_id=v.id,parent_version_id=v.parent_version_id,snapshot_id=snap.id,snapshot_digest=snap.digest,
                  validation=report.verdict,status=v.status,source=v.source,proof=v.proof)
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/f'AutoShift_{data.year}-{data.month:02d}_{v.source}_{v.id[:8]}_{uuid4().hex[:8]}.xlsx'
    with TemporaryDirectory() as tmp:
        stage=Path(tmp)/'report.xlsx'
        write_full_report(stage,data,codes,report)
        wb=load_workbook(stage)
        for row,e in enumerate(sorted(data.employees,key=lambda e:e.id),2):
            wb['Schedule'].cell(row,2).data_type='s'
        guide=wb['Huong dan']
        start=max(guide.max_row+3,42)
        header(guide,start,['AutoShift 5.0','Metadata'])
        for r,(k,value) in enumerate(meta.items(),start+1):
            guide.cell(r,1,k)
            guide.cell(r,2,str(value))
        if changes or v.source=='REPAIR':
            ws=wb.create_sheet('Thay doi')
            header(ws,1,['ID','Ngày','Trước','Sau'])
            for r,c in enumerate(changes,2): body(ws,r,[c.employee_id,str(c.day),c.before,c.after])
            widths(ws,[12,18,14,14])
            orange=Side(style='medium',color='D97706')
            ids={e.id:j for j,e in enumerate(sorted(data.employees,key=lambda e:e.id))}
            for c in changes:
                wb['Lich truc'].cell(ids[c.employee_id]+6,c.day.day+3).border=Border(left=orange,right=orange,top=orange,bottom=orange)
                wb['Schedule'].cell(ids[c.employee_id]+2,c.day.day+3).border=Border(left=orange,right=orange,top=orange,bottom=orange)
            ws.cell(len(changes)+4,1,'Số ô thay đổi')
            ws.cell(len(changes)+4,2,len(changes))
            if stored.get('repair'):
                ws.cell(len(changes)+5,1,'Vector REPAIR')
                ws.cell(len(changes)+5,2,str(stored['repair']['vector']))
        buffer=BytesIO()
        wb.save(buffer)
        wb.close()
    with path.open('xb') as f: f.write(buffer.getvalue())
    return path


def export_annual(service,plan_id,directory,employee_id=None):
    from .annual import annual_statistics
    data=annual_statistics(service,plan_id)
    wb=Workbook()
    ws=wb.active
    ws.title='Tong hop nam'
    people=[e for e in data['employees'] if employee_id is None or e['employee_id']==employee_id]
    columns=list(people[0]) if people else ['employee_id','name']
    header(ws,1,columns)
    for r,e in enumerate(people,2): body(ws,r,[str(e[k]) if isinstance(e[k],list) else e[k] for k in columns])
    widths(ws,[18]*len(columns))
    personal=wb.create_sheet('Lich ca nhan')
    header(personal,1,['ID','Ngày','Mã','Version'])
    for r,cell in enumerate([x for x in data['personal'] if employee_id is None or x['employee_id']==employee_id],2):
        body(personal,r,[cell['employee_id'],cell['day'],cell['code'],cell['version_id']])
    widths(personal,[10,18,10,42])
    meta=wb.create_sheet('Huong dan')
    meta.append(['Kế hoạch',str(data['plan'])])
    meta.append(['Lưu ý','Mã OFF đầy đủ; không suy diễn ô chưa có lịch. Version và snapshot tra cứu qua API.'])
    meta.append(['Tháng','Version','Input snapshot','Validator','Trạng thái'])
    for month in data['months']:
        meta.append([f'{month["year"]}-{month["month"]:02d}',month.get('current_version_id'),month.get('snapshot_id'),
                     month.get('validation_status','CHƯA CÓ LỊCH'),month.get('version_status')])
    widths(meta,[18,42,42,20,18])
    path=Path(directory)/f'AutoShift_annual_{plan_id}_{uuid4().hex[:10]}.xlsx'
    path.parent.mkdir(parents=True,exist_ok=True)
    buf=BytesIO()
    wb.save(buf)
    wb.close()
    with path.open('xb') as f:f.write(buf.getvalue())
    return path
