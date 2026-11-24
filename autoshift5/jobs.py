"""Own only processes launched by this server; cancel without touching unrelated PIDs."""
import subprocess
import sys
import threading
from pathlib import Path
from sqlalchemy import select
from . import models as m
from .domain import now
from .db import ROOT
from .repository import require


class JobManager:
    def __init__(self, service, db_path):
        self.service = service
        self.db_path = Path(db_path).resolve()
        self.processes = {}
        self.lock = threading.Lock()

    def launch(self, job_id):
        try:
            proc = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'autoshift5.worker', str(self.db_path), job_id],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            with self.lock:
                self.processes[job_id] = proc
            threading.Thread(target=self._watch, args=(job_id, proc), daemon=True).start()
        except Exception as exc:
            with self.service.sessions.begin() as s:
                j = require(s, m.SolverRun, job_id)
                j.status, j.error, j.finished_at = 'FAILED', str(exc), now()

    def _watch(self, job_id, proc):
        code = proc.wait()
        with self.lock:
            self.processes.pop(job_id, None)
        with self.service.sessions.begin() as s:
            j = s.get(m.SolverRun, job_id)
            if j and j.status in ('QUEUED','RUNNING','CANCELLING'):
                j.status = 'CANCELLED' if j.cancel_requested else 'FAILED'
                j.error = None if j.cancel_requested else f'Worker kết thúc bất thường, exit={code}'
                j.finished_at = now()

    def cancel(self, job_id):
        with self.service.sessions.begin() as s:
            j = require(s, m.SolverRun, job_id)
            if j.status not in ('QUEUED','RUNNING','CANCELLING'):
                return self.service.job(job_id)
            j.cancel_requested, j.status = True, 'CANCELLING'
        with self.lock:
            proc = self.processes.get(job_id)
        if proc and proc.poll() is None:
            proc.terminate()
        else:
            with self.service.sessions.begin() as s:
                j = require(s, m.SolverRun, job_id)
                j.status, j.finished_at = 'CANCELLED', now()
        return self.service.job(job_id)

    def recover(self):
        with self.service.sessions.begin() as s:
            for j in s.scalars(select(m.SolverRun).where(m.SolverRun.status.in_(['QUEUED','RUNNING','CANCELLING']))):
                j.cancel_requested=True
                j.status, j.error, j.finished_at = 'INTERRUPTED', 'Server/worker trước đã dừng. Có thể chạy lại; không tái dùng chứng minh chưa lưu.', now()

    def shutdown(self):
        with self.lock:
            owned = list(self.processes)
        for job_id in owned:
            self.cancel(job_id)
