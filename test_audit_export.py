"""Independent workbook reader and boundary regression tests (offline)."""
import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from core import Store, UTC
from excel_export import export, Sheet
from test_core import profile, t


class ExportAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.s = Store(self.path / 'test.db')

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def load(self, path, data_only=True):
        try:
            import openpyxl
        except ImportError:
            self.skipTest('Independent XLSX reader openpyxl unavailable')
        return openpyxl.load_workbook(path, data_only=data_only)

    def test_empty_workbook_and_invalid_period(self):
        target = self.path / 'report.xlsx'
        export(self.s, target)
        wb = self.load(target)
        self.assertEqual(wb['Аккаунты']['A5'].value, 'Нет аккаунтов в выбранном периоде')
        self.assertEqual(wb['Настоящий друг']['A5'].value, 'Нет добавленных аккаунтов')
        self.assertIsNone(wb['Аккаунты'].auto_filter.ref)
        wb.close()
        original = target.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Период не найден'):
            export(self.s, target, 1000)
        self.assertEqual(target.read_bytes(), original)

    def test_friend_sheet_uses_latest_saved_reading_before_first_period(self):
        pid = self.s.add_player('Александр')
        self.s.add_account(pid, profile(value=100), t())
        self.s.recalculate({'#P0Y8Q2L': profile(value=180)}, t(2))
        self.assertIsNone(self.s.latest())
        target = self.path / 'before-start.xlsx'
        export(self.s, target)
        wb = self.load(target)
        friend_sheet = wb['Настоящий друг']
        self.assertEqual([friend_sheet[f'{col}5'].value for col in 'ACDEF'],
                         ['Александр', '#P0Y8Q2L', 'Не начат', 180, 'Не начат'])
        wb.close()

    def test_xml_unicode_and_formula_like_names_are_literal(self):
        sheet = Sheet('Тест', [30])
        sheet.row(1, ['=HYPERLINK("https://example.com") & <🎯>\x00\ufffe\uffff\ud800'])
        root = ET.fromstring(sheet.xml())
        ns = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        self.assertIsNone(root.find('.//s:f', ns))
        text = root.find('.//s:t', ns).text
        self.assertEqual(text, '=HYPERLINK("https://example.com") & <🎯>')

    def test_history_dates_archived_account_and_independent_totals(self):
        pid = self.s.add_player('Глава 🎯')
        self.s.add_account(pid, profile(value=100), t())
        first = self.s.start({'#P0Y8Q2L':profile(value=100)}, t(31,10))
        self.s.archive({'#P0Y8Q2L':profile(value=150)}, ['#P0Y8Q2L'], time=t(1,11))
        self.s.add_account(pid, profile(value=900), t(2,11))
        self.s.finish({'#P0Y8Q2L':profile(value=950)}, t(3,11))
        self.s.start({'#P0Y8Q2L':profile(value=1000)}, t(4,11))
        self.s.recalculate({'#P0Y8Q2L':profile(value=1020)}, t(5,11))
        target=self.path/'history.xlsx'
        with patch('excel_export.now', return_value=t(7,11)):
            export(self.s,target,first)
        with zipfile.ZipFile(target) as z:
            for name in z.namelist(): ET.fromstring(z.read(name))
        wb=self.load(target)
        self.assertEqual(wb['Аккаунты']['D5'].value,100)
        friend_sheet=wb['Настоящий друг']
        self.assertEqual([friend_sheet[f'{col}5'].value for col in 'DEF'],[850,950,100])
        self.assertEqual(wb['За всё время']['B5'].value,120)
        self.assertEqual(wb['Итоги по игрокам']['C5'].value,100)
        self.assertEqual(wb['За всё время']['D5'].value,100)
        self.assertEqual(wb['За всё время']['E5'].value,first)
        self.assertEqual(wb['Периоды']['F5'].value,'31.10.2026 03:00')
        self.assertEqual(wb['Периоды']['G5'].value,'03.11.2026 03:00')
        self.assertEqual(wb['Периоды']['G6'].value,'Идёт')
        # Cached period sums must equal the event ledger used by Excel formulas.
        ledger={}
        for row in wb['Журнал'].iter_rows(min_row=5,values_only=True):
            ledger[(row[5],row[3])] = ledger.get((row[5],row[3]),0)+row[4]
        for row in wb['Периоды'].iter_rows(min_row=5,values_only=True):
            self.assertEqual(row[2],ledger.get((row[3],row[1]),0))
        self.assertIn('независимо от выбранного периода',wb['Аккаунты']['A9'].value)
        wb.close()

        wb=self.load(target,data_only=False)
        self.assertEqual(wb['Настоящий друг']['F5'].value,'=E5-D5')
        wb.close()

    def test_ranking_places_follow_donation_and_ties(self):
        accounts={'Александр':'#P0Y8Q2L','Борис':'#Q0Y8Q2L','Юрий':'#R0Y8Q2L'}
        for name, key in accounts.items():
            self.s.add_account(self.s.add_player(name),profile(key,100),t())
        self.s.add_player('Игрок без аккаунта')
        sid=self.s.start({key:profile(key,100) for key in accounts.values()},t(2))
        self.s.recalculate({key:profile(key,100+delta) for key,delta in zip(accounts.values(),[0,1500,1500])},t(3))
        target=self.path/'ranking.xlsx'
        export(self.s,target,sid)
        wb=self.load(target)
        self.assertEqual(wb.active.freeze_panes,'A5')
        self.assertEqual(wb.active['C3'].value,3000)
        self.assertEqual(wb.active['C5'].number_format,'#,##0')
        self.assertEqual(list(wb.active.iter_rows(min_row=5,max_row=8,max_col=4,values_only=True)),
                         [(1,'Борис',1500,1),(1,'Юрий',1500,1),(3,'Александр',0,1),('—','Игрок без аккаунта',0,0)])
        self.assertEqual(wb['Аккаунты'].auto_filter.ref,'A4:I7')
        wb.close()

    def test_write_failure_leaves_existing_report_and_removes_temp(self):
        target=self.path/'existing.xlsx'
        target.write_bytes(b'original report')
        publish='rename' if os.name=='nt' else 'link'
        with patch(f'excel_export.os.{publish}',side_effect=PermissionError('Excel holds file')):
            with self.assertRaises(PermissionError): export(self.s,target)
        self.assertEqual(target.read_bytes(),b'original report')
        self.assertEqual(list(self.path.glob('*.xlsx')),[target])

    def test_existing_report_cannot_be_replaced(self):
        target=self.path/'existing.xlsx'
        target.write_bytes(b'original report')
        with self.assertRaises(FileExistsError):
            export(self.s,target)
        self.assertEqual(target.read_bytes(),b'original report')
        self.assertEqual(list(self.path.glob('*.xlsx')),[target])


if __name__ == '__main__':
    unittest.main(verbosity=2)
