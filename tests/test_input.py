import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from core.data.data_loader import load_data, basic_warnings
from core.data.calendar_utils import standard_credit, employee_credit, previous_days

ROOT=Path(__file__).resolve().parents[1]

class InputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=load_data(ROOT/'tests/fixtures/input/input.xlsx', ROOT/'tests/fixtures/config/rules.json')
    def test_groups(self):
        self.assertEqual(len(self.data.employees),21)
        self.assertEqual(sum(e.is_leader for e in self.data.employees),6)
        self.assertEqual(sum(e.group=='SHIFT' for e in self.data.employees),16)
    def test_credit(self):
        self.assertEqual(standard_credit(self.data),23)
        d=replace(self.data,holidays=frozenset({date(2026,12,5),date(2026,12,7)}))
        self.assertEqual(standard_credit(d),22)
    def test_credit_codes(self):
        e=next(e for e in self.data.employees if e.id==13)
        r=employee_credit(self.data,e)
        self.assertEqual((r['CT'],r['DT'],r['BKD'],r['required_shifts'],r['remaining_shifts']),(2,1,1,20,19))
    def test_history(self):
        self.assertEqual(len(self.data.previous),105)
        self.assertEqual(previous_days(2027,1)[-1],date(2026,12,31))
    def test_warning(self):
        missing=replace(self.data,rules={**self.data.rules,'shift_hours':None})
        self.assertTrue(any('giờ' in w for w in basic_warnings(missing)))
        if self.data.rules['shift_hours'] is not None:
            self.assertFalse(any('Chưa khai báo giờ' in w for w in basic_warnings(self.data)))

if __name__=='__main__': unittest.main()
