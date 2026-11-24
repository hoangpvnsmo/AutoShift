import unittest
from dataclasses import replace
from pathlib import Path
from datetime import timedelta
from core.data.data_loader import load_data
from core.engine.checkpoint import read_checkpoint, fingerprints
from core.objectives.preference_checks import load_preferences
from core.engine.engine_checkpoint import model_builder_matches, migrate_checkpoint
from core.engine.engine_config import STAGES
from validator import RULES,BY_ID,Kind,Finding,RuleResult,Status,Severity,validate_structure,checkpoint_cells

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT/'tests/fixtures'

class Validator51Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=load_data(FIX/'input/input.xlsx',FIX/'config/rules.json')
        cls.cp=read_checkpoint(FIX/'output/checkpoints/2026-12_real.json')
        cls.cells=list(checkpoint_cells(cls.data,cls.cp.assignments))
    def report(self,cells=None,data=None):
        return validate_structure(data or self.data,self.cells if cells is None else cells)
    def test_registry_unique(self): self.assertEqual(len(RULES),len(BY_ID))
    def test_registry_sources_exist(self):
        for r in RULES:
            for src in r.source: self.assertTrue((ROOT/src.split(':')[0]).is_file(),src)
    def test_objective_order_and_flags(self):
        self.assertEqual([r.id for r in RULES if r.kind==Kind.OBJECTIVE],['STAGE3','E01','E02','E03a','E03b','E04a','E04b','E05','E06'])
        self.assertEqual([r.flag for r in RULES if r.kind==Kind.OBJECTIVE],[s.flag for s in STAGES])
    def test_actual_grid(self):
        self.assertEqual(len(self.cells),651)
        report=self.report(); self.assertEqual(report.verdict,'INCOMPLETE')
        self.assertEqual([r.rule_id for r in report.results if r.status==Status.PASS],['INPUT_GRID','INPUT_HISTORY'])
        self.assertIn('B_WORK_TIME',report.hard_pending)
        self.assertEqual(len(report.errors),0)
    def test_actual_training(self):
        self.assertEqual([c for i,d,c in self.cells if i==5 and d.day in (15,16)],['DT','DT'])
    def test_missing(self): self.assertEqual(self.report(self.cells[1:]).verdict,'ERROR')
    def test_duplicate(self): self.assertEqual(self.report(self.cells+[self.cells[0]]).verdict,'ERROR')
    def test_unknown_id(self):
        _,d,c=self.cells[0]; self.assertEqual(self.report(self.cells+[(9999,d,c)]).verdict,'ERROR')
    def test_blank(self):
        i,d,_=self.cells[0]; self.assertEqual(self.report([(i,d,None)]+self.cells[1:]).verdict,'ERROR')
    def test_bad_type(self):
        i,d,_=self.cells[0]; self.assertEqual(self.report([(i,d,1)]+self.cells[1:]).verdict,'ERROR')
    def test_mapping(self): self.assertEqual(self.report({(i,d):c for i,d,c in self.cells}).verdict,'INCOMPLETE')
    def test_missing_history(self):
        p=dict(self.data.previous); p.pop(next(iter(p)))
        self.assertEqual(self.report(data=replace(self.data,previous=p)).verdict,'ERROR')
    def test_conflicting_training_not_silently_changed(self):
        a=dict(self.cp.assignments); key=next(k for k in a if k[0]==5 and k[1].day==16); a[key]=2
        self.assertEqual(dict(((i,d),c) for i,d,c in checkpoint_cells(self.data,a))[key],'2')
    def test_hard_fail_needs_error(self):
        with self.assertRaises(ValueError): RuleResult('A03',Status.FAIL,'bad')
    def test_soft_not_error(self):
        with self.assertRaises(ValueError): RuleResult('E06',Status.FAIL,'bad',(Finding('E06',Severity.ERROR,'bad'),))
    def test_source_hash_unchanged(self):
        self.assertTrue(model_builder_matches(ROOT))
        prefs=load_preferences(FIX/'input/input.xlsx',self.data)
        migrated,v=migrate_checkpoint(self.cp,self.data,prefs,ROOT,fingerprints(self.data,prefs,ROOT))
        self.assertEqual(v,'475'); self.assertEqual(migrated.proven_prefix,7)
    def test_json_report(self):
        import json
        json.dumps(self.report().to_dict(),default=str)

if __name__=='__main__': unittest.main()
