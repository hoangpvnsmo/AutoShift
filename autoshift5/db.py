from pathlib import Path
from contextlib import contextmanager
from contextlib import closing
import sqlite3
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker
from .models import Base, State, WorkCode, ShiftType, AuditLog
from .domain import CODES, Conflict, canonical

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data' / 'autoshift.db'


def make_engine(path=DEFAULT_DB):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine('sqlite:///' + path.as_posix(), connect_args={'check_same_thread': False, 'timeout': 15}, json_serializer=canonical)
    @event.listens_for(engine, 'connect')
    def connect(dbapi, _):
        dbapi.isolation_level = None
        cur = dbapi.cursor()
        cur.execute('PRAGMA foreign_keys=ON')
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA busy_timeout=15000')
        cur.close()
    @event.listens_for(engine, 'begin')
    def begin(conn):
        conn.exec_driver_sql('BEGIN')
    return engine


def initialize(engine):
    """Alembic owns production DDL; this also supports isolated test databases."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        install_guards(conn)
    with sessionmaker(engine).begin() as s:
        if s.get(State, 1) is None:
            s.add(State(id=1, revision=1))
        for c in CODES:
            if s.get(WorkCode, c) is None:
                s.add(WorkCode(code=c, credit=int(c not in ('OFF', 'BKD'))))
        s.flush()
        for c, name in [('1', 'Ca đêm'), ('2', 'Ca sáng'), ('3', 'Ca chiều')]:
            if s.get(ShiftType, c) is None:
                s.add(ShiftType(code=c, name=name))


def install_guards(conn):
    for table in ('schedule_assignments', 'input_snapshots', 'rule_profiles', 'schedule_changes'):
        for action in ('UPDATE', 'DELETE'):
            conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS protect_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable history'); END")
    conn.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS protect_confirmed_versions BEFORE UPDATE ON schedule_versions
        WHEN OLD.status IN ('PLANNED','PUBLISHED','ACTUAL')
        BEGIN SELECT RAISE(ABORT, 'confirmed version is immutable; create a child'); END""")
    conn.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS protect_version_delete BEFORE DELETE ON schedule_versions BEGIN SELECT RAISE(ABORT, 'immutable history'); END")
    conn.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS protect_confirmed_assignment_insert BEFORE INSERT ON schedule_assignments
        WHEN (SELECT status FROM schedule_versions WHERE id=NEW.version_id) != 'DRAFT'
        BEGIN SELECT RAISE(ABORT, 'confirmed assignments are immutable'); END""")


def revision(s) -> int:
    return s.get(State, 1).revision


def bump(s, expected=None) -> int:
    old = revision(s)
    if expected is not None and old != expected:
        raise Conflict(f'Dữ liệu đã đổi: revision {old}, yêu cầu {expected}. Tải lại trước khi lưu.')
    count = s.execute(update(State).where(State.id == 1, State.revision == old).values(revision=old+1)).rowcount
    if count != 1:
        raise Conflict('Dữ liệu vừa được thay đổi bởi thao tác khác.')
    return old+1


def audit(s, action, entity, payload):
    s.add(AuditLog(action=action, entity_id=str(entity), payload=payload))


def backup(source, destination):
    """SQLite online backup includes WAL, unlike copying a live .db file."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
