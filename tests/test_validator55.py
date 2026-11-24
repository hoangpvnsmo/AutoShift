import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
from openpyxl import load_workbook
from core.data.data_loader import load_data
from core.engine.checkpoint import read_checkpoint,source_hashes
from core.objectives.preference_checks import load_preferences
from validator import checkpoint_cells,validate_quality,score_vector,read_schedule,write_schedule,Status
from validator.__main__ import main
from contextlib import redirect_stdout
from io import StringIO
import json
ROOT=Path(__file__).resolve().parents[1]; FIX=ROOT/'tests/fixtures'

class Validator55Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=load_data(FIX/'input/input.xlsx',FIX/'config/rules.json')
        cls.cp=read_checkpoint(FIX/'output/checkpoints/2026-12_real.json')
        from dataclasses import replace
        cls.cp=replace(cls.cp,scores=cls.cp.scores[:7]+((1,85,160,29),)+cls.cp.scores[8:])
        cls.cells=tuple(checkpoint_cells(cls.data,cls.cp.assignments))
        cls.prefs=load_preferences(FIX/'input/input.xlsx',cls.data)
    def setUp(self):
        self.tmp=TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'schedule.xlsx'
        write_schedule(self.path,self.data,self.cells)
    def edit(self,fn):
        wb=load_workbook(self.path)
        try:
            fn(wb['Schedule']); wb.save(self.path)
        finally: wb.close()
    def report(self): return validate_quality(self.data,read_schedule(self.path,self.data),prefs=self.prefs,source='excel_schedule')
    def rule(self,r,k): return next(x for x in r.results if x.rule_id==k)
    def test_roundtrip(self):
        r=self.report(); self.assertEqual(score_vector(r),self.cp.scores); self.assertFalse(r.errors); self.assertEqual(r.source,'excel_schedule')
        self.assertEqual(set(read_schedule(self.path,self.data)),set(self.cells))
    def test_numeric_shift(self):
        self.edit(lambda s:setattr(s['D2'],'value',1)); self.assertEqual(dict(((i,d),c) for i,d,c in read_schedule(self.path,self.data))[1,date(2026,12,1)],'1')
    def test_blank(self):
        self.edit(lambda s:setattr(s['D2'],'value',None)); self.assertEqual(self.rule(self.report(),'INPUT_GRID').status,Status.FAIL)
    def test_bad_code(self):
        self.edit(lambda s:setattr(s['D2'],'value','DT+1')); self.assertEqual(self.rule(self.report(),'INPUT_GRID').status,Status.FAIL)
    def test_formula(self):
        self.edit(lambda s:setattr(s['D2'],'value','=1')); self.assertRaisesRegex(ValueError,'D2',read_schedule,self.path,self.data)
    def test_wrong_month(self):
        self.edit(lambda s:setattr(s['D1'],'value','2026-11-01')); self.assertRaisesRegex(ValueError,'D1',read_schedule,self.path,self.data)
    def test_excel_date(self):
        self.edit(lambda s:setattr(s['D1'],'value',date(2026,12,1))); self.assertEqual(score_vector(self.report()),self.cp.scores)
    def test_duplicate_date(self):
        self.edit(lambda s:setattr(s['E1'],'value','2026-12-01')); self.assertRaises(ValueError,read_schedule,self.path,self.data)
    def test_duplicate_employee(self):
        self.edit(lambda s:s.append([c.value for c in s[2]])); self.assertEqual(self.rule(self.report(),'INPUT_GRID').status,Status.FAIL)
    def test_missing_employee(self):
        self.edit(lambda s:s.delete_rows(2)); self.assertEqual(self.rule(self.report(),'INPUT_GRID').status,Status.FAIL)
    def test_unknown_employee(self):
        self.edit(lambda s:setattr(s['A2'],'value',999)); self.assertEqual(self.rule(self.report(),'INPUT_GRID').status,Status.FAIL)
    def test_group_mismatch(self):
        self.edit(lambda s:setattr(s['C2'],'value','ADMIN')); self.assertRaisesRegex(ValueError,'ID 1',read_schedule,self.path,self.data)
    def test_extra_column(self):
        self.edit(lambda s:setattr(s['AI2'],'value','OFF')); self.assertRaisesRegex(ValueError,'ngoài',read_schedule,self.path,self.data)
    def test_training_not_reconstructed(self):
        self.edit(lambda s:setattr(s.cell(6,18),'value','OFF')) # ID5, Dec15
        r=self.report(); self.assertEqual(self.rule(r,'C_REQUEST_NONSHIFT').status,Status.FAIL)
        self.assertNotEqual(score_vector(r),self.cp.scores)
    def test_staff_removed(self):
        self.edit(lambda s:setattr(s['D2'],'value','OFF')); self.assertEqual(self.rule(self.report(),'A02').status,Status.FAIL)
    def test_no_overwrite(self):
        before=self.path.read_bytes(); self.assertRaises(FileExistsError,write_schedule,self.path,self.data,self.cells); self.assertEqual(before,self.path.read_bytes())
    def test_readonly(self):
        before=self.path.read_bytes(); self.report(); self.assertEqual(before,self.path.read_bytes())
    def test_cli_json(self):
        b=StringIO()
        with redirect_stdout(b): rc=main(['--root',str(FIX),'--schedule',str(self.path),'--json'])
        self.assertEqual(rc,3); payload=json.loads(b.getvalue()); self.assertEqual(payload['source'],'excel_schedule'); self.assertEqual(payload['errors'],0)
    def test_cli_export(self):
        path=Path(self.tmp.name)/'out.xlsx'; b=StringIO()
        with redirect_stdout(b): rc=main(['--root',str(FIX),'--export-schedule',str(path),'--json'])
        self.assertEqual(rc,3); self.assertTrue(path.exists()); self.assertEqual(set(read_schedule(path,self.data)),set(self.cells))
    def test_bad_sheet(self):
        self.edit(lambda s:setattr(s,'title','Other')); self.assertRaisesRegex(ValueError,'Schedule',read_schedule,self.path,self.data)
    def test_source_hash_unchanged(self):
        before=source_hashes(ROOT); self.report(); self.assertEqual(before,source_hashes(ROOT))

if __name__=='__main__': unittest.main()
