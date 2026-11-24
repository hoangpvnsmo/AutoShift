"""Local-only FastAPI transport. Business decisions stay in the service layer."""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import json
import os
from fastapi import FastAPI, HTTPException, Request as HttpRequest
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError, OperationalError
from . import models as m
from . import schemas as dto
from .db import make_engine, initialize, DEFAULT_DB, ROOT, revision, bump, audit
from .domain import Conflict, Unsupported, canonical, deserialize, now
from .services import Service, row_dict
from .repository import require, load_month, codes_for, validation_for
from .importer import import_legacy
from .jobs import JobManager
from .annual import create_plan, confirm_plan, annual_statistics
from .reporting import export_version, export_annual
from .validation import validate_month


def create_app(db_path=None, *, start_workers=True):
    db_path = Path(db_path or os.environ.get('AUTOSHIFT_DB', DEFAULT_DB)).resolve()
    engine = make_engine(db_path)
    service = Service(engine)
    manager = JobManager(service, db_path)
    @asynccontextmanager
    async def lifespan(app):
        initialize(engine)
        if start_workers:
            manager.recover()
        yield
        manager.shutdown()
        engine.dispose()
    app = FastAPI(title='AutoShift 5.0', version='5.0.0', lifespan=lifespan,
                  description='Quản lý lịch trực tại localhost. Solver tạo bản nháp; xác nhận là thao tác riêng.')
    app.state.service, app.state.jobs = service, manager
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost','127.0.0.1','testserver','[::1]'])
    origins = ['http://localhost:5173','http://127.0.0.1:5173','http://localhost:8765','http://127.0.0.1:8765']
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET','POST','PUT','DELETE'], allow_headers=['Content-Type'])

    @app.middleware('http')
    async def local_origin(request, call_next):
        if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('origin') and request.headers['origin'] not in [*origins,str(request.base_url).rstrip('/')]:
            return JSONResponse(status_code=403,content={'detail':'Chỉ nhận thay đổi từ giao diện localhost đã cấu hình.'})
        return await call_next(request)

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse(status_code=409, content={'detail':str(exc)})
    @app.exception_handler(Unsupported)
    async def unsupported(_, exc):
        return JSONResponse(status_code=422, content={'detail':str(exc)})
    @app.exception_handler(ValueError)
    async def bad_input(_, exc):
        return JSONResponse(status_code=422, content={'detail':str(exc)})
    @app.exception_handler(IntegrityError)
    async def integrity(_, exc):
        return JSONResponse(status_code=409, content={'detail':'Dữ liệu trùng, xung đột hoặc lịch sử bất biến: '+str(exc.orig)})
    @app.exception_handler(OperationalError)
    async def busy(_, exc):
        return JSONResponse(status_code=409, content={'detail':'CSDL đang được thay đổi; tải lại rồi thử lại.'})

    @app.get('/api/health')
    def health() -> dict:
        return {**service.state(),'database':str(db_path),'solver':'OR-Tools CP-SAT','time_zone':'Asia/Bangkok'}

    @app.post('/api/import')
    def import_input(payload:dto.ImportInput) -> dict:
        return import_legacy(engine,payload.excel,payload.rules,payload.schedule,dry_run=payload.dry_run)

    @app.get('/api/months')
    def months() -> list[dict]: return service.months()

    @app.get('/api/months/{month_id}/calendar')
    def calendar(month_id:int,version_id:str|None=None) -> dict:
        return service.calendar(month_id,version_id)

    @app.get('/api/employees')
    def employees() -> list[dict]:
        with service.sessions() as s:
            return [row_dict(e) for e in s.scalars(select(m.Employee).order_by(m.Employee.id))]

    @app.put('/api/employees')
    def employee(payload:dto.EmployeeInput) -> dict: return service.employee(payload)

    @app.delete('/api/months/{month_id}/employees/{employee_id}')
    def remove_employee(month_id:int,employee_id:int,revision:int) -> dict:
        with service.sessions.begin() as s:
            bump(s,revision)
            month=require(s,m.PlanningMonth,month_id)
            if month.current_version_id:
                raise Conflict('Tháng đã có lịch: giữ nhân sự để bảo toàn lịch sử. Tạo kế hoạch tháng mới với danh sách nhân sự mới.')
            setting=require(s,m.MonthlySetting,(month_id,employee_id))
            s.delete(setting)
            audit(s,'REMOVE_MONTH_EMPLOYEE',employee_id,{'month_id':month_id})
            return {'revision':revision+1}

    @app.get('/api/requests')
    def requests() -> list[dict]: return service.requests()

    @app.post('/api/requests/batch')
    def request_batch(payload:dto.RequestBatch) -> dict: return service.request_batch(payload)

    @app.post('/api/requests/{request_id}/status')
    def request_status(request_id:str,payload:dto.RequestTransition) -> dict:
        return service.request_transition(request_id,payload)

    @app.get('/api/requests/{request_id}/revisions')
    def request_revisions(request_id:str) -> list[dict]:
        with service.sessions() as s:
            require(s,m.Request,request_id)
            return [row_dict(r) for r in s.scalars(select(m.RequestRevision).where(m.RequestRevision.request_id==request_id).order_by(m.RequestRevision.revision))]

    @app.put('/api/preferences')
    def preference(payload:dto.PreferenceInput) -> dict: return service.set_preference(payload)

    @app.delete('/api/preferences/{preference_id}')
    def delete_preference(preference_id:int,revision:int) -> dict:
        with service.sessions.begin() as s:
            bump(s,revision)
            s.delete(require(s,m.Preference,preference_id))
            audit(s,'DELETE_PREFERENCE',preference_id,{})
            return {'revision':revision+1}

    @app.get('/api/preferences')
    def preferences() -> list[dict]:
        with service.sessions() as s:
            return [row_dict(p) for p in s.scalars(select(m.Preference).order_by(m.Preference.day))]

    @app.get('/api/months/{month_id}/rules')
    def rules(month_id:int) -> dict: return service.rules(month_id)

    @app.put('/api/months/{month_id}/rules')
    def set_rules(month_id:int,payload:dto.MonthConfig) -> dict: return service.rules(month_id,payload)

    @app.put('/api/months/{month_id}/configuration')
    def set_calendar(month_id:int,payload:dto.CalendarConfig) -> dict: return service.calendar_config(month_id,payload)

    @app.put('/api/months/{month_id}/validation-work-policy')
    def validation_work_policy(month_id:int,payload:dto.WorkTimeInput) -> dict:
        from validator.working_time import parse_work_policy
        with service.sessions.begin() as s:
            bump(s,payload.revision)
            month=require(s,m.PlanningMonth,month_id)
            data,_=load_month(s,month_id)
            raw=payload.policy.model_dump(mode='json',exclude_none=True)
            parse_work_policy(data,raw)
            prior=require(s,m.RuleProfile,month.rule_profile_id)
            profile=m.RuleProfile(name=f'Giờ phi ca tháng {month_id} rev {revision(s)}',revision=revision(s),rules=prior.rules,work_policy=raw)
            s.add(profile);s.flush();month.rule_profile_id=profile.id
            audit(s,'VALIDATOR_WORK_TIME_POLICY',profile.id,raw)
            return {'revision':revision(s),'profile_id':profile.id,'scope':'VALIDATOR_ONLY',
                    'notice':'Không thêm rule giờ phi ca vào mô hình Solver. Kết quả bị từ chối nếu Validator phát hiện vi phạm.'}

    @app.put('/api/locks')
    def lock(payload:dto.LockInput) -> dict: return service.lock(payload)

    @app.post('/api/jobs',response_model=dto.JobView,status_code=202)
    def enqueue(payload:dto.JobInput):
        job=service.enqueue(payload)
        if start_workers: manager.launch(job['id'])
        return job

    @app.get('/api/jobs',response_model=list[dto.JobView])
    def jobs(): return service.jobs()

    @app.get('/api/jobs/{job_id}',response_model=dto.JobView)
    def job(job_id:str): return service.job(job_id)

    @app.post('/api/jobs/{job_id}/cancel',response_model=dto.JobView)
    def cancel(job_id:str): return manager.cancel(job_id)

    @app.get('/api/jobs/{job_id}/events')
    async def events(job_id:str,request:HttpRequest):
        service.job(job_id)
        async def stream():
            last=None
            while not await request.is_disconnected():
                current=await asyncio.to_thread(service.job,job_id)
                encoded=canonical(current)
                if encoded!=last:
                    yield 'data: '+encoded+'\n\n'
                    last=encoded
                if current['status'] not in ('QUEUED','RUNNING','CANCELLING'):
                    break
                await asyncio.sleep(.75)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})

    @app.post('/api/versions/{version_id}/validate')
    def validate(version_id:str) -> dict:
        with service.sessions() as s:
            v=require(s,m.ScheduleVersion,version_id)
            snap=require(s,m.InputSnapshot,v.snapshot_id)
            data,prefs=deserialize(snap.payload)
            codes=codes_for(s,v.id)
            from validator.quality import validate_quality
            report=validate_quality(data,codes,prefs=prefs,work_policy=snap.work_policy)
            try: boundaries=service._check_neighbors(s,data,codes)
            except Conflict as exc: boundaries=[{'verdict':'ERROR','message':str(exc)}]
            return {**report.to_dict(),'history_gaps':len(__import__('autoshift5.domain',fromlist=['history_gaps']).history_gaps(data)),
                    'boundaries':boundaries,'proof':v.proof,'scope':'SNAPSHOT'}

    @app.get('/api/versions/{version_id}/snapshot')
    def get_snapshot(version_id:str) -> dict:
        with service.sessions() as s:
            v=require(s,m.ScheduleVersion,version_id)
            return row_dict(require(s,m.InputSnapshot,v.snapshot_id))

    @app.get('/api/versions/compare')
    def compare(left:str,right:str) -> dict: return service.compare(left,right)

    @app.post('/api/versions/{version_id}/confirm')
    def confirm(version_id:str,payload:dto.ConfirmInput) -> dict: return service.confirm(version_id,payload)

    @app.post('/api/versions/{version_id}/edit')
    def edit(version_id:str,payload:dto.DraftEdit) -> dict: return service.edit_draft(version_id,payload)

    @app.post('/api/versions/{version_id}/restore')
    def restore(version_id:str,payload:dto.Revision) -> dict: return service.edit_draft(version_id,payload,restore=True)

    @app.get('/api/versions/{version_id}/export')
    def export(version_id:str):
        path=export_version(service,version_id,ROOT/'artifacts'/'exports')
        return FileResponse(path,filename=path.name,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @app.get('/api/annual-plans')
    def plans() -> list[dict]:
        with service.sessions() as s:
            return [row_dict(p) for p in s.scalars(select(m.AnnualPlan).order_by(m.AnnualPlan.year,m.AnnualPlan.id))]

    @app.post('/api/annual-plans')
    def create_annual(payload:dto.AnnualInput) -> dict: return create_plan(service,payload)

    @app.get('/api/annual-plans/{plan_id}')
    def annual(plan_id:int) -> dict: return annual_statistics(service,plan_id)

    class AnnualConfirm(dto.Revision):
        version_ids:list[str]

    @app.post('/api/annual-plans/{plan_id}/confirm')
    def accept_annual(plan_id:int,payload:AnnualConfirm) -> dict:
        return confirm_plan(service,plan_id,payload.version_ids,payload.revision)

    @app.get('/api/annual-plans/{plan_id}/export')
    def export_plan(plan_id:int,employee_id:int|None=None):
        path=export_annual(service,plan_id,ROOT/'artifacts'/'exports',employee_id)
        return FileResponse(path,filename=path.name)

    @app.get('/api/audit')
    def audit_history() -> list[dict]:
        with service.sessions() as s:
            return [row_dict(a) for a in s.scalars(select(m.AuditLog).order_by(m.AuditLog.id.desc()).limit(200))]

    dist=ROOT/'frontend'/'dist'
    if (dist/'assets').is_dir(): app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
    @app.get('/{path:path}',include_in_schema=False)
    def frontend(path:str):
        if path.startswith('api/') or not (dist/'index.html').is_file():
            raise HTTPException(404,'Chưa có frontend build. Chạy setup_autoshift.ps1 hoặc npm run build trong frontend.')
        return FileResponse(dist/'index.html')
    return app


app=create_app()
