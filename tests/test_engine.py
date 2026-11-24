import ast
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import shutil
import unittest
from importlib.util import find_spec
from core.engine.engine_config import EngineOptions, STAGES, KEYS
from core.engine.engine_checkpoint import (UNIFIED_CORE, historical_fingerprints, migrate_checkpoint,
                                    choose_checkpoint, model_builder_matches)
from core.engine.checkpoint import Checkpoint, fingerprints, LAYERS, LENGTHS, reusable_prefix
from core.data.models import MonthData, Employee, Request
from core.objectives.preference_checks import Preference, preference_rows, preference_score
from core.data.calendar_utils import month_days
from core.data.data_loader import load_data
from core.validation.credit_checks import demand_requirements
from core.validation.admin_checks import check_admin_credits
from core.objectives.quality_checks import workforce_score, shift_balance_rows, shift_balance_score
from core.objectives.night_checks import night_rows, night_score
from core.objectives.day_balance_checks import day_balance_rows
from core.objectives.day_balance_v2 import day_score_v2
from core.objectives.rest_checks import rest_rows, rest_scores, rest_windows
from core.objectives.flexible_rotation import flexible_rows, flexible_score
from core.reporting.schedule_report import collect_report, print_report
from run_autoshift import build_parser, options_from_args

ROOT=Path(__file__).resolve().parents[1]
D=date(2026,12,1)
S=tuple(tuple(0 for _ in range(n)) for n in LENGTHS)

class ConfigTests(unittest.TestCase):
    def test_stage_order_and_budget(self):
        self.assertEqual(tuple(s.key for s in STAGES),LAYERS)
        self.assertEqual(EngineOptions().budget_for('rotation'),60)
        self.assertEqual(EngineOptions().budget_for('preferences'),30)
    def test_modes(self):
        self.assertEqual(options_from_args(build_parser().parse_args([])).search_mode,'ALNS')
        self.assertEqual(options_from_args(build_parser().parse_args(['--fixed-lns'])).search_mode,'LNS')
        self.assertEqual(options_from_args(build_parser().parse_args(['--no-lns'])).search_mode,'GLOBAL')
    def test_options_validation(self):
        for kw in ({'base_time':0},{'night_time':float('nan')},{'ideal_time':-1},{'lns_fraction':1},{'lns_slice':0},{'stall_limit':2},{'validation_time':0}):
            with self.subTest(kw=kw),self.assertRaises(ValueError): EngineOptions(**kw)
    def test_cli_params(self):
        o=options_from_args(build_parser().parse_args(['--fresh','--rotation-time','120','--lns-seed','123','--lns-stall','6']))
        self.assertEqual((o.fresh,o.rotation_time,o.lns_seed,o.stall_limit),(True,120,123,6))
    def test_builder_unchanged(self):
        def builder(p): return ast.dump(next(x for x in ast.parse(p.read_text(encoding='utf-8-sig')).body if isinstance(x,ast.FunctionDef) and x.name=='build_stage3'),include_attributes=False)
        self.assertEqual(builder(ROOT/'tests/reference_stage3.py'),builder(ROOT/'core/model/engine_model.py'))
        self.assertTrue(model_builder_matches(ROOT))

class PersistenceTests(unittest.TestCase):
    def setUp(self):
        tmp=TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root=Path(tmp.name)
        shutil.copytree(ROOT/'core',self.root/'core',ignore=shutil.ignore_patterns('__pycache__'))
        for f in ('run_autoshift.py','release_manifest.json'): shutil.copy2(ROOT/f,self.root/f)
        self.data=MonthData(2026,12,frozenset(),(Employee(1,'A','SHIFT',True,(1,2,3)),),(),{}, {})
        self.h=fingerprints(self.data,(),self.root)
    def cp(self,version='474',prefix=7):
        return Checkpoint(*historical_fingerprints(self.data,(),self.root,version),{(1,D):2},S,prefix)
    def test_exact_historical_hash(self):
        import json
        manifest=json.loads((self.root/'release_manifest.json').read_text(encoding='utf-8'))
        from core.engine.checkpoint import fingerprints_from_sources
        self.assertEqual(historical_fingerprints(self.data,(),self.root,'475'),
                         fingerprints_from_sources(self.data,(),manifest['legacy_sources']))
        self.assertNotEqual(historical_fingerprints(self.data,(),self.root,'475'), self.h)
    def test_migrate_three_versions(self):
        for version in ('472','473','474','475'):
            old=self.cp(version); new,v=migrate_checkpoint(old,self.data,(),self.root,self.h)
            self.assertEqual(v,version); self.assertEqual(new.base_hash,self.h[0]); self.assertEqual(new.scores,S)
    def test_preferences_changed(self):
        prefs=(Preference(1,D,'PREFER_OFF'),); h=fingerprints(self.data,prefs,self.root)
        new,v=migrate_checkpoint(self.cp(prefix=9),self.data,prefs,self.root,h)
        self.assertEqual(v,'474'); self.assertEqual(reusable_prefix(new,*h,S),7)
    def test_hard_changed_no_proof(self):
        old=self.cp(); data=replace(self.data,requests=(Request(1,D,'DT'),)); h=fingerprints(data,(),self.root)
        new,v=migrate_checkpoint(old,data,(),self.root,h)
        self.assertIsNone(v); self.assertEqual(reusable_prefix(new,*h,S),0)
    def test_modified_new_engine_no_migration(self):
        old=self.cp(); p=self.root/'core/engine/objective_engine.py'; p.write_bytes(p.read_bytes()+b'\n# change\n')
        h=fingerprints(self.data,(),self.root); self.assertIsNone(migrate_checkpoint(old,self.data,(),self.root,h)[1])
    def test_modified_old_source_no_migration(self):
        old=self.cp(); p=self.root/'core/objectives/rotation_checks.py'; p.write_bytes(p.read_bytes()+b'\n# change\n')
        h=fingerprints(self.data,(),self.root); self.assertIsNone(migrate_checkpoint(old,self.data,(),self.root,h)[1])
    def test_priority_protection(self):
        old=self.cp(); a=(True,)*9
        worse=replace(old,scores=S[:7]+((1,0,0,0),(0,)))
        with self.assertRaises(RuntimeError): choose_checkpoint(worse,S,7,a,False)
        self.assertEqual(choose_checkpoint(worse,S,7,a,True)[1],'KEPT_OLD')
        self.assertEqual(choose_checkpoint(old,worse.scores,7,a,False)[1],'SAVED')
    def test_retain_proof_on_equal(self):
        old=self.cp(prefix=7); chosen,action,*_=choose_checkpoint(old,S,8,(True,)*9,False)
        self.assertEqual((chosen.proven_prefix,action),(8,'SAVED_EQUAL_PROOF'))

def sample_data():
    data=load_data(ROOT/'tests/fixtures/input/input.xlsx',ROOT/'tests/fixtures/config/rules.json')
    # The distributed training workbook/config may not yet have actual shift hours.
    if data.rules.get('shift_hours') is None:
        data.rules['shift_hours']={str(s):{'start':a,'end':b,'start_day_offset':o} for s,a,b,o in [(1,'22:00','06:00',-1),(2,'06:00','14:00',0),(3,'14:00','22:00',0)]}
    return data


def fixture():
    data=sample_data()
    # Six people per shift, staggered 3-on/3-off; two remain from previous day.
    people=tuple(Employee(i+1,f'E{i+1}','SHIFT',True,(i//6+1,)) for i in range(18))
    days=month_days(2026,12)
    def slot(i,d): return (i-1)//6+1 if ((d-D).days+(i-1)%6)%6<3 else 0
    a={(e.id,d):slot(e.id,d) for e in people for d in days}
    prev={(e.id,D-timedelta(days=k)):str(slot(e.id,D-timedelta(days=k))) if slot(e.id,D-timedelta(days=k)) else 'OFF' for e in people for k in range(1,6)}
    data=replace(data,year=2026,month=12,employees=people,previous=prev,requests=(),holidays=frozenset())
    prefs=(Preference(1,D,'PREFER_SHIFT',1),)
    rows=check_admin_credits(data,days,a)[2]; dr=day_balance_rows(data,days,a); rw=rest_windows(data,days)
    scores=(workforce_score(rows),shift_balance_score(shift_balance_rows(data,days,a)),night_score(night_rows(data,days,a)),day_score_v2(dr,'holiday'),day_score_v2(dr,'weekend'),*rest_scores(rest_rows(data,days,a,rw),len(rw.weekends)),flexible_score(flexible_rows(data,days,a)),preference_score(preference_rows(data,days,a,prefs)))
    q=SimpleNamespace(workforce=scores[0],baseline=SimpleNamespace(status='OPTIMAL'),selected=SimpleNamespace(assignments=a),rest_b_lower=None,preference_lower=(0,))
    for k,s in zip(KEYS,scores[1:]): setattr(q,k,SimpleNamespace(before=s,after=s,status='FEASIBLE'))
    return data,days,a,prefs,scores,q

class ReportTests(unittest.TestCase):
    def test_full_report(self):
        data,days,a,prefs,scores,q=fixture()
        r=collect_report(data,days,demand_requirements(data),a,prefs,q)
        self.assertEqual(r.scores,scores); self.assertEqual(len(r.codes),18*31); self.assertEqual(r.total_demand,279)
        lines=[]; print_report(r,days,q,lambda *x:lines.append(' '.join(map(str,x))))
        self.assertTrue(any('93 ca đúng nhu cầu' in s for s in lines))
    def test_reject_invalid_schedule(self):
        data,days,a,prefs,_,q=fixture(); a[1,D]=0
        with self.assertRaises(RuntimeError): collect_report(data,days,demand_requirements(data),a,prefs,q)
    def test_reject_wrong_score(self):
        data,days,a,prefs,_,q=fixture(); q.rotation.after=(999,0,0,0)
        with self.assertRaisesRegex(RuntimeError,'rotation'): collect_report(data,days,demand_requirements(data),a,prefs,q)

@unittest.skipUnless(find_spec('ortools') is not None,'Thiếu OR-Tools')
class IntegrationTests(unittest.TestCase):
    def test_model_parity(self):
        from core.model.engine_model import build_stage3
        from tests.reference_stage3 import build_stage3 as old
        data=sample_data()
        a,*_=build_stage3(data); b,*_=old(data)
        self.assertEqual(str(a.model.proto),str(b.model.proto))
    def test_resume_and_changed_preferences(self):
        from core.engine.autoshift_engine import run_schedule
        from core.engine.checkpoint import read_checkpoint
        data=sample_data()
        disabled={s.flag:False for s in STAGES[1:-1]}
        data=replace(data,rules={**data.rules,'optimization':disabled})
        opts=EngineOptions(base_time=30,preference_time=5,lns_seed=1)
        quiet=lambda *x:None
        with TemporaryDirectory() as td:
            path=Path(td)/'test.json'
            r=run_schedule(data,root=ROOT,options=opts,checkpoint_file=path,emit=quiet)
            self.assertIsNotNone(r.assignments); self.assertEqual(r.candidate.proven_prefix,9)
            again=run_schedule(data,root=ROOT,options=opts,checkpoint_file=path,emit=quiet)
            self.assertEqual(again.reused_prefix,9); self.assertEqual(again.assignments,r.assignments)
            i,d=next(iter(r.assignments)); s=next(s for s in (1,2,3) if s!=r.assignments[i,d])
            changed=run_schedule(data,(Preference(i,d,'AVOID_SHIFT',s),),root=ROOT,options=opts,checkpoint_file=path,emit=quiet)
            self.assertEqual(changed.reused_prefix,8); self.assertEqual(changed.report.scores[-1],(0,))
            self.assertEqual(read_checkpoint(path).proven_prefix,9)
