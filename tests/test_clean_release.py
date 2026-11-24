"""Regression tests on the exact uploaded 4.7.5 checkpoint, independent of live inputs."""
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import shutil
from core.data.data_loader import load_data
from core.data.calendar_utils import month_days
from core.objectives.preference_checks import load_preferences, Preference
from core.engine.checkpoint import fingerprints, read_checkpoint, reusable_prefix
from core.engine.engine_checkpoint import migrate_checkpoint, model_builder_matches
from core.engine.engine_config import KEYS
from core.reporting.schedule_report import collect_report
from core.validation.credit_checks import demand_requirements
from core.data.models import Request

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures'

class CleanReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=load_data(FIX/'input/input.xlsx', FIX/'config/rules.json')
        cls.prefs=load_preferences(FIX/'input/input.xlsx',cls.data)
        cls.cp=read_checkpoint(FIX/'output/checkpoints/2026-12_real.json')

    def test_actual_checkpoint_and_scores(self):
        cp=self.cp
        hashes=fingerprints(self.data,self.prefs,ROOT)
        migrated,version=migrate_checkpoint(cp,self.data,self.prefs,ROOT,hashes)
        self.assertEqual(version,'475')
        self.assertEqual(migrated.assignments,cp.assignments)
        q=SimpleNamespace(workforce=cp.scores[0])
        new_scores=cp.scores[:7]+((1,85,160,29),)+cp.scores[8:]
        for k,s in zip(KEYS,new_scores[1:]):
            setattr(q,k,SimpleNamespace(before=s,after=s,status='FEASIBLE'))
        report=collect_report(self.data,month_days(self.data.year,self.data.month),demand_requirements(self.data),cp.assignments,self.prefs,q)
        self.assertEqual(len(report.codes),651)
        self.assertEqual(report.scores,new_scores)
        self.assertEqual(report.scores[-2:],((1,85,160,29),(1,)))
        self.assertEqual(reusable_prefix(migrated,*hashes,report.scores),7)

    def test_change_hard_data_invalidates_proof(self):
        r=self.data.requests[0]
        d=replace(self.data,requests=tuple(x for x in self.data.requests if x!=r))
        h=fingerprints(d,self.prefs,ROOT)
        old,version=migrate_checkpoint(self.cp,d,self.prefs,ROOT,h)
        self.assertIsNone(version)
        self.assertEqual(reusable_prefix(old,*h,self.cp.scores),0)

    def test_change_soft_data_preserves_base_migration(self):
        prefs=self.prefs[:-1]
        h=fingerprints(self.data,prefs,ROOT)
        cp,v=migrate_checkpoint(self.cp,self.data,prefs,ROOT,h)
        self.assertEqual(v,'475')
        self.assertNotEqual(cp.pref_hash,h[1])
        self.assertEqual(reusable_prefix(cp,*h,self.cp.scores),7)

    def test_nested_source_change_blocks_migration(self):
        with TemporaryDirectory() as td:
            r=Path(td)
            shutil.copytree(ROOT/'core',r/'core',ignore=shutil.ignore_patterns('__pycache__'))
            for n in ('release_manifest.json','run_autoshift.py'): shutil.copy2(ROOT/n,r/n)
            self.assertTrue(model_builder_matches(r))
            old=fingerprints(self.data,self.prefs,r)
            p=r/'core/constraints/hard_constraints.py'
            p.write_bytes(p.read_bytes()+b'\n# changed\n')
            h=fingerprints(self.data,self.prefs,r)
            self.assertNotEqual(old[0],h[0])
            self.assertFalse(model_builder_matches(r))
            self.assertIsNone(migrate_checkpoint(self.cp,self.data,self.prefs,r,h)[1])

    def test_no_old_cli(self):
        self.assertEqual([p.name for p in ROOT.glob('run_*.py')],['run_autoshift.py'])
