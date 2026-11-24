from pathlib import Path
import argparse
import json
from alembic.config import Config
from alembic import command
from .db import make_engine, initialize, DEFAULT_DB, ROOT, backup
from .importer import import_legacy

parser=argparse.ArgumentParser(description='AutoShift 5.0 local management')
parser.add_argument('--db',type=Path,default=DEFAULT_DB)
sub=parser.add_subparsers(dest='action',required=True)
sub.add_parser('init')
imp=sub.add_parser('import')
imp.add_argument('--excel',type=Path,default=ROOT/'input/input.xlsx')
imp.add_argument('--rules',type=Path,default=ROOT/'config/rules.json')
imp.add_argument('--schedule',type=Path)
imp.add_argument('--commit',action='store_true',help='Mặc định dry-run; thêm --commit để lưu.')
bak=sub.add_parser('backup')
bak.add_argument('destination',type=Path)
args=parser.parse_args()
if args.action=='backup':
    backup(args.db,args.destination)
    print(args.destination)
else:
    cfg=Config(str(ROOT/'alembic.ini'))
    cfg.set_main_option('script_location',str(ROOT/'migrations'))
    cfg.attributes['db_path']=args.db
    command.upgrade(cfg,'head')
    engine=make_engine(args.db)
    initialize(engine)
    if args.action=='import':
        result=import_legacy(engine,args.excel,args.rules,args.schedule,dry_run=not args.commit)
        print(json.dumps({k:v for k,v in result.items() if k!='source_rows'},ensure_ascii=False,indent=2,default=str))
    else: print('SQLite initialized:',args.db)
