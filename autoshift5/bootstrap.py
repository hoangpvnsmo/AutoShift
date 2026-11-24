"""Import the original workbook once; existing SQLite data always wins."""
from sqlalchemy import select, func
from .db import make_engine, DEFAULT_DB, ROOT
from .models import PlanningMonth
from .services import Service
from .importer import import_legacy

engine=make_engine(DEFAULT_DB)
service=Service(engine)
with service.sessions() as s:
    count=s.scalar(select(func.count()).select_from(PlanningMonth))
if not count:
    schedules=sorted((ROOT/'output').glob('AutoShift_*_4_9_*.xlsx'))
    schedule=schedules[-1] if schedules else None
    report=import_legacy(engine,ROOT/'input/input.xlsx',ROOT/'config/rules.json',schedule,dry_run=False)
    print('Imported:',report['month'],'| exact field equivalence:',report['field_equivalence'])
else:
    print('SQLite already contains planning data. No files re-imported.')
engine.dispose()
