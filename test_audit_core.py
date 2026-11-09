"""Regression scenarios from the clan-leader reliability audit. Offline fixtures only."""
import datetime as dt
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from core import Store, sync_cwl, UTC

A = '#P0Y8Q2L'
B = '#Q0Y8Q2L'
CLAN = '#QYPG'

def time(day, month=11):
    return dt.datetime(2026, month, day, tzinfo=UTC)

def profile(key=A, value=100):
    return {'tag': key, 'name': 'Воин', 'achievements': [
        {'name': 'Friend in Need', 'value': value, 'village': 'home'}]}

class AuditAccounting(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.temp.name) / 'test.db')
        self.pid = self.s.add_player('Глава')
        self.s.add_account(self.pid, profile(), time(1))
    def tearDown(self):
        self.s.db.close()
        self.temp.cleanup()
    def test_restore_player_preserves_identity_and_excludes_absence(self):
        self.s.start({A: profile(value=100)}, time(2))
        self.s.archive({A: profile(value=150)}, [A], self.pid, time(3))
        self.assertEqual(self.s.archived_players()[0]['id'], self.pid)
        self.s.restore_player(self.pid)
        self.assertEqual(self.s.accounts(), [])
        self.s.add_account(self.pid, profile(value=1000), time(4))
        self.s.finish({A: profile(value=1100)}, time(5))
        self.assertEqual(self.s.summary()[0]['total'], 150)
        self.assertEqual(self.s.db.execute('SELECT SUM(amount) FROM events').fetchone()[0], 150)
    def test_restore_name_collision_is_rejected(self):
        self.s.archive({}, [A], self.pid, time(2))
        self.s.add_player('Глава')
        with self.assertRaises(ValueError): self.s.restore_player(self.pid)
        self.assertEqual(len(self.s.archived_players()), 1)
    def test_readd_stale_or_decreasing_snapshot_keeps_archive(self):
        self.s.archive({}, [A], time=time(2))
        for p, stamp in ((profile(value=101), time(1)), (profile(value=99), time(3))):
            with self.assertRaises(ValueError): self.s.add_account(self.pid, p, stamp)
            self.assertEqual(self.s.accounts(), [])
    def test_zero_finished_period_is_valid_record(self):
        sid = self.s.start({A: profile()}, time(2))
        self.s.finish({A: profile()}, time(3))
        self.assertEqual((self.s.summary()[0]['best_period'], self.s.summary()[0]['best']), (sid, 0))
    def test_missing_second_account_rolls_back_finish_and_start(self):
        self.s.add_account(self.pid, profile(B, 500), time(1))
        with self.assertRaises(KeyError): self.s.start({A: profile(value=200)}, time(2))
        self.assertIsNone(self.s.latest())
        self.assertEqual(self.s.accounts()[0]['friend'], 100)
        self.s.start({A: profile(), B: profile(B, 500)}, time(2))
        with self.assertRaises(KeyError): self.s.finish({A: profile(value=200)}, time(3))
        self.assertIsNotNone(self.s.running())
        self.assertEqual(self.s.summary()[0]['total'], 0)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 0)
    def test_archive_batch_failure_rolls_back_player_and_accounts(self):
        self.s.add_account(self.pid, profile(B, 500), time(1))
        self.s.start({A: profile(), B: profile(B, 500)}, time(2))
        with self.assertRaises(ValueError):
            self.s.archive({A: profile(value=200), B: profile(B, 400)}, [A, B], self.pid, time(3))
        self.assertEqual(len(self.s.accounts()), 2)
        self.assertEqual(len(self.s.players()), 1)
        self.assertEqual(self.s.summary()[0]['total'], 0)
    def test_repeated_remove_restore_cycles_exclude_all_gaps(self):
        self.s.start({A: profile()}, time(2))
        for end_day, end_value, add_value in ((3, 120, 500), (5, 550, 1000)):
            self.s.archive({A: profile(value=end_value)}, [A], time=time(end_day))
            self.s.add_account(self.pid, profile(value=add_value), time(end_day + 1))
        self.s.finish({A: profile(value=1030)}, time(7))
        self.assertEqual(self.s.summary()[0]['total'], 100)
        self.assertEqual(self.s.db.execute('SELECT SUM(amount) FROM events').fetchone()[0], 100)
    def test_manual_hint_does_not_claim_cwl_participation(self):
        with patch('core.now', return_value=time(6)):
            self.s.set_cwl_clan(A, CLAN)
            self.assertEqual(self.s.get_cwl_clan(A), CLAN)
            self.assertEqual(self.s.rows()[0]['cwl_clan'], 'Нет данных')
            self.assertIsNone(self.s.rows()[0]['stars'])
            self.s.set_cwl_clan(A, None)
            self.assertIsNone(self.s.get_cwl_clan(A))
    def test_hint_is_only_for_selected_season_and_active_account(self):
        with patch('core.now', return_value=time(6)):
            self.s.set_cwl_clan(A, CLAN)
        with patch('core.now', return_value=time(6, 12)):
            self.assertIsNone(self.s.get_cwl_clan(A))
            calls=[]
            class API:
                def get(self, path): calls.append(path); return {'season': '2026-12'}
            self.assertEqual(sync_cwl(self.s, API()), [])
            self.assertEqual(calls, [])
        self.s.archive({}, [A], time=time(7))
        with self.assertRaises(ValueError): self.s.set_cwl_clan(A, CLAN)
    def test_departed_account_saved_roster_is_refreshed_and_wars_deduped(self):
        self.s.save_group({'season': '2026-11-02', 'clans': [
            {'tag': CLAN, 'name': 'Аркадия', 'members': [{'tag': A}]}]})
        calls=[]
        class API:
            def get(self, path):
                calls.append(path)
                if '/leaguegroup' in path:
                    return {'season': '2026-11-02', 'rounds': [{'warTags': ['#WAR', '#0']}]}
                return {'state': 'warEnded', 'clan': {'tag': CLAN, 'name': 'Аркадия',
                    'members': [{'tag': A, 'attacks': [{'stars': 3}]}]}}
        with patch('core.now', return_value=time(6)):
            self.assertEqual(sync_cwl(self.s, API()), [])
            self.assertEqual(sync_cwl(self.s, API()), [])
            self.assertEqual(self.s.rows()[0]['stars'], 3)
        self.assertIn('/clans/%23QYPG/currentwar/leaguegroup', calls)
    def test_backup_rejects_working_database_and_hardlink(self):
        with self.assertRaisesRegex(ValueError, 'рабочей базы'):
            self.s.backup(self.s.path)
        alias = Path(self.temp.name) / 'same-database.db'
        try:
            alias.hardlink_to(self.s.path)
        except (OSError, NotImplementedError):
            return
        with self.assertRaisesRegex(ValueError, 'рабочей базы'):
            self.s.backup(alias)
        self.assertEqual(self.s.accounts()[0]['friend'], 100)
    def test_import_restores_previous_version_and_keeps_local_backup(self):
        original = self.s
        original.start({A: profile()}, time(2))
        original.recalculate({A: profile(value=180)}, time(3))
        with original.db:
            original.db.execute('DROP TABLE cwl_hints')
            original.db.execute('INSERT INTO clans VALUES(?)', (CLAN,))
        snapshot = Path(self.temp.name) / 'копия #1.sqlite3'
        original.backup(snapshot)
        destination_path = Path(self.temp.name) / 'destination.sqlite3'
        destination = Store(destination_path)
        self.addCleanup(destination.db.close)
        destination.add_player('Местный игрок')
        previous = destination.import_backup(snapshot)
        self.assertEqual(destination.running()['id'], 1)
        self.assertEqual(destination.summary()[0]['total'], 80)
        self.assertEqual(destination.db.execute('SELECT tag FROM clans').fetchone()[0], CLAN)
        self.assertEqual(destination.db.execute('SELECT COUNT(*) FROM cwl_hints').fetchone()[0], 0)
        with sqlite3.connect(previous) as db:
            self.assertEqual(db.execute('SELECT name FROM players').fetchone()[0], 'Местный игрок')
        with sqlite3.connect(destination_path) as db:
            self.assertEqual(db.execute('SELECT last_value FROM entries').fetchone()[0], 180)
        with sqlite3.connect(snapshot) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_schema WHERE name='cwl_hints'").fetchone())

    def test_import_rejects_other_files_without_changing_working_database(self):
        root = Path(self.temp.name)
        unrelated = root / 'unrelated.db'
        with sqlite3.connect(unrelated) as db:
            db.execute('CREATE TABLE unrelated(data TEXT)')
        invalid = root / 'invalid.sqlite3'
        invalid.write_bytes(b'not a SQLite database')
        for path in (unrelated, invalid, self.s.path):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.s.import_backup(path)
        self.assertEqual(self.s.accounts()[0]['friend'], 100)
        self.assertEqual(list(root.glob('Clash_before_import_*')), [])

    def test_opening_previous_database_only_adds_hint_table(self):
        self.s.start({A: profile()}, time(2))
        self.s.finish({A: profile(value=400)}, time(3))
        self.s.start({A: profile(value=500)}, time(4))
        self.s.recalculate({A: profile(value=550)}, time(5))
        with self.s.db:
            self.s.db.execute('DROP TABLE cwl_hints')
        snapshot={name: [tuple(r) for r in self.s.db.execute('SELECT * FROM '+name)]
                  for name in ('players','accounts','sessions','entries','events','rosters','wars')}
        self.s.db.close()
        self.s=Store(Path(self.temp.name) / 'test.db')
        for name, data in snapshot.items():
            self.assertEqual([tuple(r) for r in self.s.db.execute('SELECT * FROM '+name)], data)
        summary=self.s.summary()[0]
        self.assertEqual((summary['total'],summary['current'],summary['best']), (350,50,300))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM cwl_hints').fetchone()[0], 0)
    def test_invalid_group_response_does_not_erase_data(self):
        with self.s.db: self.s.db.execute('INSERT INTO clans VALUES(?)', (CLAN,))
        class API:
            def get(self, path): return {'unexpected': True}
        self.assertTrue(sync_cwl(self.s, API()))
        self.assertEqual(self.s.accounts()[0]['friend'], 100)

if __name__ == '__main__': unittest.main(verbosity=2)
