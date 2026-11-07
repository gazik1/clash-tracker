import datetime as dt
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from core import Store,API,UTC,friend,tag
from excel_export import export

def t(day=1,month=10,hour=0): return dt.datetime(2026,month,day,hour,tzinfo=UTC)
def profile(key='#P0Y8Q2L',value=100,nick='Воин'):
    return {'tag':key,'name':nick,'achievements':[{'name':'Friend in Need','value':value,'village':'home'}]}

class Accounting(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.s=Store(self.root/'test.db'); self.pid=self.s.add_player('Дима')
        self.s.add_account(self.pid,profile(),t())
    def tearDown(self): self.s.db.close(); self.temp.cleanup()
    def values(self,n,key='#P0Y8Q2L'): return {key:profile(key,n)}
    def test_start_is_fresh_baseline_and_recount_idempotent(self):
        self.s.start(self.values(120),t(2)); self.s.recalculate(self.values(180),t(3)); self.s.recalculate(self.values(180),t(4))
        self.assertEqual(self.s.rows()[0]['total'],60); self.assertEqual(self.s.summary()[0]['total'],60)
    def test_multiple_accounts_and_new_account_mid_period(self):
        self.s.start(self.values(100),t(2)); self.s.add_account(self.pid,profile('#Q0Y8Q2L',500),t(3))
        values=self.values(160)|self.values(700,'#Q0Y8Q2L'); self.s.finish(values,t(4))
        self.assertEqual(sum(a['total'] for a in self.s.rows()),260); self.assertEqual(self.s.summary()[0]['total'],260)
    def test_period_pause_excluded_and_history_persists(self):
        sid=self.s.start(self.values(100),t(2)); self.s.finish(self.values(200),t(3))
        self.s.start(self.values(500),t(4)); self.s.finish(self.values(550),t(5))
        self.assertEqual(self.s.summary()[0]['total'],150); self.assertEqual(self.s.rows(sid)[0]['total'],100)
    def test_archive_retains_counts_and_readd_does_not_count_gap(self):
        self.s.start(self.values(100),t(2)); self.s.archive(self.values(180),['#P0Y8Q2L'],time=t(3))
        self.assertEqual(len(self.s.accounts()),0); self.assertEqual(self.s.rows()[0]['total'],80)
        self.s.add_account(self.pid,profile(value=500),t(4)); self.s.finish(self.values(550),t(5))
        self.assertEqual(self.s.summary()[0]['total'],130); self.assertEqual(self.s.rows()[0]['total'],130)
    def test_player_archive_rename_and_database_reopen(self):
        self.s.start(self.values(100),t(2)); self.s.archive(self.values(250),['#P0Y8Q2L'],self.pid,t(3))
        self.assertEqual(len(self.s.players()),0); self.assertEqual(self.s.summary()[0]['total'],150)
        self.s.db.close(); self.s=Store(self.root/'test.db'); self.assertEqual(self.s.summary()[0]['total'],150)
    def test_atomic_rollback_if_one_account_fails(self):
        self.s.add_account(self.pid,profile('#Q0Y8Q2L',500),t()); self.s.start(self.values(100)|self.values(500,'#Q0Y8Q2L'),t(2))
        with self.assertRaises(ValueError): self.s.finish(self.values(180)|self.values(400,'#Q0Y8Q2L'),t(3))
        self.assertIsNotNone(self.s.running()); self.assertEqual(self.s.summary()[0]['total'],0)
        self.assertEqual(self.s.db.execute('SELECT friend FROM accounts WHERE tag=?',('#P0Y8Q2L',)).fetchone()[0],100)
    def test_manual_period_crosses_month_and_becomes_record_on_finish(self):
        first = self.s.start(self.values(100), t(31,10,20))
        self.s.recalculate(self.values(400), t(1,11,1))
        self.assertEqual(self.s.running()['id'], first)
        self.assertEqual(self.s.summary()[0]['current'], 300)
        self.assertIsNone(self.s.summary()[0]['best_period'])

        self.s.finish(self.values(450), t(2,11))
        second = self.s.start(self.values(550), t(3,11))
        self.s.recalculate(self.values(950), t(4,11))
        result = self.s.summary()[0]
        self.assertEqual((result['current'], result['total']), (400, 750))
        self.assertEqual((result['best_period'], result['best']), (first, 350))
        self.assertEqual(self.s.summary(first)[0]['current'], 350)

        self.s.finish(self.values(950), t(5,11))
        self.assertEqual(self.s.summary()[0]['best_period'], second)
    def test_cwl_roster_wars_are_deduplicated(self):
        self.s.save_group({'season':'2026-11','clans':[{'tag':'#QYPG','name':'Аркадия','members':[{'tag':'#P0Y8Q2L'}]}]})
        war={'state':'inWar','clan':{'tag':'#QYPG','name':'Аркадия','members':[{'tag':'#P0Y8Q2L','attacks':[{'stars':2}]}]}}
        self.s.save_war('#WAR','2026-11',war); war['clan']['members'][0]['attacks'][0]['stars']=3
        self.s.save_war('#WAR','2026-11',war)
        self.assertEqual(self.s.db.execute('SELECT SUM(stars) FROM wars').fetchone()[0],3)
    def test_missing_friend_is_rejected(self):
        with self.assertRaises(ValueError): friend({'achievements':[]})
        with self.assertRaises(ValueError): tag('abcXYZ')
    def test_report_layout_formulas_and_escaped_strings(self):
        self.s.rename(self.pid,'=SUM(1,2) & <Дима>')
        self.s.add_account(self.pid,profile('#Q0Y8Q2L',500,'Игрок <2>'),t())
        self.s.start(self.values(100)|self.values(500,'#Q0Y8Q2L'),t(2))
        self.s.finish(self.values(180)|self.values(800,'#Q0Y8Q2L'),t(3))
        first = self.s.latest()['id']
        self.s.start(self.values(180)|self.values(800,'#Q0Y8Q2L'), t(4))
        self.s.recalculate(self.values(280)|self.values(800,'#Q0Y8Q2L'), t(5))
        path=self.root/'report.xlsx'; export(self.s,path,first)
        with zipfile.ZipFile(path) as z:
            self.assertIsNone(z.testzip())
            for name in z.namelist(): ET.fromstring(z.read(name))
            xml=z.read('xl/worksheets/sheet2.xml').decode()
            self.assertIn('<autoFilter ref="A4:H6"/>',xml)
            self.assertNotIn('A5:A6',xml)
            self.assertIn('=SUM(1,2) &amp; &lt;Дима&gt;',xml)
        # Independently verify filters, formulas and cached values.
        try: import openpyxl
        except ImportError: return
        wb=openpyxl.load_workbook(path,data_only=False)
        self.assertEqual(wb.active['C3'].value,'=SUM(C5:C5)')
        self.assertEqual(wb['Периоды']['C5'].value,"=SUMIFS('Журнал'!E$5:E$7,'Журнал'!F$5:F$7,D5,'Журнал'!D$5:D$7,B5)")
        wb.close(); wb=openpyxl.load_workbook(path,data_only=True)
        accounts=wb['Аккаунты']
        self.assertEqual([accounts[f'A{i}'].value for i in (5,6)],['=SUM(1,2) & <Дима>']*2)
        self.assertEqual([accounts[f'C{i}'].value for i in (5,6)],['#P0Y8Q2L','#Q0Y8Q2L'])
        self.assertEqual([accounts[f'D{i}'].value for i in (5,6)],[80,300])
        self.assertEqual(wb['За всё время']['B5'].value,480)
        self.assertEqual(wb.active['C5'].value,380)
        self.assertEqual(wb.active.title,'Итоги по игрокам')
        self.assertEqual(wb.active['C3'].value,380)
        self.assertEqual(wb.active['D5'].value,2)
        self.assertEqual(wb.active['E5'].value,480)
        self.assertEqual(wb['За всё время']['E5'].value,first)
        self.assertEqual(wb.active.max_row,5)
        self.assertEqual(wb['Периоды']['C6'].value,100)
        wb.close()
    def test_backup_is_consistent(self):
        self.s.start(self.values(100),t(2)); self.s.recalculate(self.values(300),t(3))
        self.s.backup(self.root/'backup.db'); backup=Store(self.root/'backup.db')
        self.assertEqual(backup.summary()[0]['total'],200); backup.db.close()
    def test_requests_are_parallel_and_failure_rejects_batch(self):
        import threading,time
        api=API('test'); lock=threading.Lock(); active=0; maximum=0
        def request(k):
            nonlocal active,maximum
            with lock: active+=1; maximum=max(maximum,active)
            time.sleep(.03)
            with lock: active-=1
            return profile(k)
        with patch.object(api,'player',side_effect=request):
            self.assertEqual(len(api.players(['#P0Y8Q2L','#Q0Y8Q2L','#L0Y8Q2L'])),3)
        self.assertGreater(maximum,1)
        with patch.object(api,'player',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError): api.players(['#P0Y8Q2L'])


class Transport(unittest.TestCase):
    def test_proxy_default_and_direct_explicit(self):
        self.assertEqual(API('test').base_url,'https://cocproxy.royaleapi.dev/v1')
        self.assertEqual(API('test','direct').base_url,'https://api.clashofclans.com/v1')
        with self.assertRaises(ValueError): API('test','https://other.invalid')
    def test_proxy_request_auth_tag_encoding(self):
        from unittest.mock import MagicMock
        response=MagicMock(); response.__enter__.return_value=response
        response.read.return_value=b'{"tag":"#P0Y8Q2L"}'
        with patch('urllib.request.urlopen',return_value=response) as send:
            self.assertEqual(API('secret').player('#P0Y8Q2L')['tag'],'#P0Y8Q2L')
            request=send.call_args.args[0]
            self.assertEqual(request.full_url,'https://cocproxy.royaleapi.dev/v1/players/%23P0Y8Q2L')
            self.assertEqual(request.get_header('Authorization'),'Bearer secret')
    def test_http_errors_timeouts_and_malformed_json(self):
        import urllib.error
        for status in (401,403,404,429,503):
            with self.subTest(status=status):
                with patch('urllib.request.urlopen',side_effect=urllib.error.HTTPError('https://test.invalid',status,'fail',{},None)):
                    with self.assertRaises(RuntimeError) as context: API('secret').get('/locations')
                    self.assertNotIn('secret',str(context.exception))
                    if status==403: self.assertIn('45.79.218.79',str(context.exception))
        with patch('urllib.request.urlopen',side_effect=TimeoutError):
            with self.assertRaisesRegex(RuntimeError,'20 секунд'): API('test').get('/locations')
        from unittest.mock import MagicMock
        response=MagicMock(); response.__enter__.return_value=response; response.read.return_value=b'not json'
        with patch('urllib.request.urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'некорректный ответ'): API('test').get('/locations')
    def test_changed_player_tag_rejects_batch(self):
        api=API('test')
        with patch.object(api,'player',return_value=profile('#Q0Y8Q2L')):
            with self.assertRaises(RuntimeError): api.players(['#P0Y8Q2L'])

class CWLRegression(unittest.TestCase):
    def test_day_qualified_season_is_displayed_in_current_month(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/'test.sqlite3'); pid=s.add_player('ЛВК'); s.add_account(pid,profile(),t())
            season=s.save_group({'season':'2026-11-02','clans':[{'tag':'#QYPG','name':'Аркадия','members':[{'tag':'#P0Y8Q2L'}]}]})
            s.save_war('#WAR',season,{'state':'warEnded','clan':{'tag':'#QYPG','name':'Аркадия','members':[{'tag':'#P0Y8Q2L','attacks':[{'stars':3}]}]}})
            with patch('core.now',return_value=t(6,11)):
                row=s.rows()[0]
                self.assertEqual(row['stars'],3); self.assertEqual(row['cwl_clan'],'Аркадия')
            s.db.close()

if __name__=='__main__': unittest.main(verbosity=2)
