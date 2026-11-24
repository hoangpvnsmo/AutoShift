import unittest
from pathlib import Path
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch
from importlib.util import find_spec
from core.data.data_loader import load_data
from core.objectives.preference_checks import load_preferences
from core.engine.checkpoint import (read_checkpoint, write_checkpoint, fingerprints,
    reusable_prefix, Checkpoint)
from core.engine.engine_checkpoint import historical_fingerprints, migrate_checkpoint
from validator.structure import checkpoint_cells
from validator.engine_gate import check_candidate, validate_and_persist, ValidationRejected
from validator.quality import score_vector
from run_autoshift import build_parser

ROOT=Path(__file__).resolve().parents[1]; FIX=ROOT/'tests/fixtures'
quiet=lambda *x: None

class Validator56Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=load_data(FIX/'input/input.xlsx',FIX/'config/rules.json')
        cls.prefs=load_preferences(FIX/'input/input.xlsx',cls.data)
        cls.cp=read_checkpoint(FIX/'output/checkpoints/2026-12_real.json')
        from dataclasses import replace
        cls.cp=replace(cls.cp,scores=cls.cp.scores[:7]+((1,85,160,29),)+cls.cp.scores[8:])
        cls.codes={(i,d):c for i,d,c in checkpoint_cells(cls.data,cls.cp.assignments)}

    def setUp(self):
        tmp=TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'old.json'; write_checkpoint(self.path,self.cp)
        self.original=self.path.read_bytes()

    def persist(self,cp=None,codes=None,**kw):
        cp=self.cp if cp is None else cp
        return validate_and_persist(self.data,self.prefs,cp,self.codes if codes is None else codes,
            path=self.path,chosen=kw.pop('chosen',cp),emit=quiet,**kw)

    def test_actual_gate(self):
        r=check_candidate(self.data,self.prefs,self.cp,self.codes)
        self.assertEqual(r.status,'LIMITED'); self.assertEqual(r.report.hard_pending,('B_WORK_TIME',))
        self.assertEqual(score_vector(r.report),self.cp.scores)
        self.assertEqual(len(r.report.errors),0)
        self.assertEqual(len(r.report.warnings),2)

    def test_write_after_gate(self):
        cp=replace(self.cp,base_hash='new')
        self.persist(cp); self.assertEqual(read_checkpoint(self.path),cp)

    def test_keep_old_no_write(self):
        self.persist(chosen=None); self.assertEqual(self.path.read_bytes(),self.original)

    def test_stateless_still_validated(self):
        with patch('validator.engine_gate.write_checkpoint') as write:
            gate=validate_and_persist(self.data,self.prefs,self.cp,self.codes,emit=quiet)
            self.assertEqual(gate.status,'LIMITED'); write.assert_not_called()

    def test_mismatched_score_rejects(self):
        cp=replace(self.cp,scores=(*self.cp.scores[:-1],(99,)))
        with self.assertRaisesRegex(ValidationRejected,'Điểm độc lập'): self.persist(cp)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_codes_mismatch_rejects(self):
        codes=dict(self.codes); key=next(k for k,v in codes.items() if v=='DT'); codes[key]='OFF'
        with self.assertRaisesRegex(ValidationRejected,'Mã lịch'): self.persist(codes=codes)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_hard_violation_rejects(self):
        a=dict(self.cp.assignments); key=next(k for k,v in a.items() if v==1); a[key]=0
        cp=replace(self.cp,assignments=a); codes={(i,d):c for i,d,c in checkpoint_cells(self.data,a)}
        with self.assertRaises(ValidationRejected) as e: self.persist(cp,codes)
        self.assertTrue(e.exception.report.errors); self.assertEqual(self.path.read_bytes(),self.original)

    def test_strict_rejects_missing_phi(self):
        with self.assertRaisesRegex(ValidationRejected,'B_WORK_TIME'): self.persist(require_complete=True)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_explicit_policy_incomplete_rejects(self):
        with self.assertRaises(ValidationRejected): self.persist(work_policy={})
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_exception_rejects(self):
        with patch('validator.engine_gate.validate_quality',side_effect=ValueError('test')):
            with self.assertRaisesRegex(ValidationRejected,'Không thể hoàn thành'): self.persist()
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_chosen_different_rejects(self):
        chosen=replace(self.cp,base_hash='other')
        with self.assertRaisesRegex(ValidationRejected,'khác nghiệm'): self.persist(chosen=chosen)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_missing_other_rule_rejects(self):
        from validator.result import RuleResult,Status
        report=check_candidate(self.data,self.prefs,self.cp,self.codes).report
        report=replace(report,results=tuple(RuleResult(r.rule_id,Status.NOT_CHECKED,'test') if r.rule_id=='B04' else r for r in report.results))
        with patch('validator.engine_gate.validate_quality',return_value=report):
            with self.assertRaisesRegex(ValidationRejected,'B04'): self.persist()
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_old_48_migration(self):
        h=historical_fingerprints(self.data,self.prefs,ROOT,'48'); new=fingerprints(self.data,self.prefs,ROOT)
        old=replace(self.cp,base_hash=h[0],pref_hash=h[1]); cp,v=migrate_checkpoint(old,self.data,self.prefs,ROOT,new)
        self.assertEqual(v,'48'); self.assertEqual(reusable_prefix(cp,*new,self.cp.scores),7)

    def test_changed_data_no_migration(self):
        h=historical_fingerprints(self.data,self.prefs,ROOT,'48'); old=replace(self.cp,base_hash=h[0],pref_hash=h[1])
        d=replace(self.data,requests=self.data.requests[:-1]); cp,v=migrate_checkpoint(old,d,self.prefs,ROOT,fingerprints(d,self.prefs,ROOT))
        self.assertIsNone(v); self.assertEqual(reusable_prefix(cp,*fingerprints(d,self.prefs,ROOT),self.cp.scores),0)

    def test_cli_flags(self):
        a=build_parser().parse_args(['--require-complete-validation','--work-time-policy','policy.json'])
        self.assertTrue(a.require_complete_validation); self.assertEqual(a.work_time_policy,Path('policy.json'))

    @unittest.skipUnless(find_spec('ortools') is not None,'Thiếu OR-Tools')
    def test_engine_gate_before_write(self):
        from core.engine.autoshift_engine import run_schedule
        from core.engine.engine_config import EngineOptions
        # Use the existing real checkpoint, short refinement; reject final gate deliberately.
        opts=EngineOptions(rotation_time=0.1,preference_time=0.1,lns_fraction=0)
        with patch('core.engine.autoshift_engine.validate_and_persist',side_effect=ValidationRejected('test')) as gate:
            with self.assertRaises(ValidationRejected):
                run_schedule(self.data,self.prefs,root=ROOT,options=opts,checkpoint_file=self.path,emit=quiet)
            gate.assert_called_once()
        self.assertEqual(self.path.read_bytes(),self.original)

if __name__=='__main__': unittest.main()
