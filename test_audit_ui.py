"""Offline failure-path checks using the real desktop and database."""
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch


class WindowRestoreLogic(unittest.TestCase):
    def test_tool_window_preserves_owner_restore_and_period_controls(self):
        from app import App
        panel=Mock()
        start,end,other=Mock(),Mock(),Mock()
        owner=SimpleNamespace(busy=False,panel=None,active_period_id=1,
            buttons=[start,end,other],start_btn=start,end_btn=end,
            state=Mock(return_value='iconic'),after_idle=Mock())
        for name in ('_open_panel','_panel_closed','_restore_panel','_raise_panel','refresh_buttons'):
            setattr(owner,name,getattr(App,name).__get__(owner))

        with patch('app.tk.Toplevel',return_value=panel) as new_window:
            self.assertIs(owner._open_panel('Настройки'),panel)
            self.assertIsNone(owner._open_panel('Настройки'))
            new_window.assert_called_once_with(owner)
        panel.transient.assert_called_once_with(owner)
        panel.grab_set.assert_not_called()
        for button in owner.buttons:
            button.configure.assert_called_with(state='disabled')
        panel.deiconify.assert_not_called()

        owner._restore_panel(SimpleNamespace(widget=panel))
        owner.after_idle.assert_not_called()
        owner.state.return_value='normal'
        owner._restore_panel(SimpleNamespace(widget=owner))
        owner.after_idle.assert_called_once_with(owner._raise_panel)
        owner.after_idle.call_args.args[0]()
        panel.deiconify.assert_called_once()
        panel.lift.assert_called_once()

        owner._panel_closed(SimpleNamespace(widget=object()))
        self.assertIs(owner.panel,panel)
        owner._panel_closed(SimpleNamespace(widget=panel))
        self.assertIsNone(owner.panel)
        start.configure.assert_called_with(state='disabled')
        end.configure.assert_called_with(state='normal')
        other.configure.assert_called_with(state='normal')


@unittest.skipUnless(os.environ.get('DISPLAY') or os.name == 'nt', 'A graphical display is needed')
class DesktopReliability(unittest.TestCase):
    def setUp(self):
        import app
        from test_core import profile, t
        self.app = app
        self.profile = profile
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.stack.enter_context(patch.multiple(app, DATA=self.root, DB=self.root/'test.db', CONFIG=self.root/'settings.json'))
        self.errors = []
        self.stack.enter_context(patch.object(app.messagebox, 'showerror', side_effect=lambda *a, **kw: self.errors.append(a)))
        self.stack.enter_context(patch.object(app.messagebox, 'showinfo'))
        self.stack.enter_context(patch.object(app.messagebox, 'askyesno', return_value=True))
        self.stack.enter_context(patch.object(app, 'sync_cwl', return_value=[]))
        self.ui = app.App()
        self.use_readable_test_fonts()
        self.ui.config_data.update(reports=str(self.root/'reports'), token='offline-test')
        self.addCleanup(self.cleanup_ui)
        self.pid = self.ui.store.add_player('Глава')
        self.key = '#P0Y8Q2L'
        self.ui.store.add_account(self.pid, profile(value=100), t())
        self.ui.store.start({self.key: profile(value=100)}, t(2))
        self.ui.refresh()
        self.ui.update()

    def use_readable_test_fonts(self):
        # Linux runtime Tk uses X11 core fonts. Prefer available Cyrillic fonts
        # for geometry tests without changing the app's native Windows fonts.
        import tkinter.font as font
        from tkinter import ttk
        if os.name == 'nt' or 'dejavu sans' not in font.families(self.ui):
            return
        self.ui.tk.call('tk', 'scaling', 1.333333)
        for name in font.names(self.ui):
            font.nametofont(name, root=self.ui).configure(family='DejaVu Sans', size=10)
        style = ttk.Style(self.ui)
        for name in ('TButton', 'TLabel', 'Treeview', 'Treeview.Heading'):
            style.configure(name, font=('DejaVu Sans', 10))
        for widget in self.descendants(self.ui):
            if 'font' in widget.keys():
                requested = self.ui.tk.splitlist(widget.cget('font'))
                size = int(requested[1]) if len(requested)>1 and str(requested[1]).lstrip('-').isdigit() else 10
                weight = 'bold' if 'bold' in requested else 'normal'
                widget.configure(font=('DejaVu Sans', size, weight))

    def cleanup_ui(self):
        if self.ui.busy:
            self.settle()
        self.ui.close()

    def settle(self, ui=None):
        if ui is None:
            ui=self.ui
        deadline = time.monotonic() + 8
        while ui.busy and time.monotonic() < deadline:
            ui.update()
            time.sleep(.01)
        ui.update()
        self.assertFalse(ui.busy, 'background job did not complete')

    def fake_api(self, failure=None, value=175):
        profile = self.profile
        class FakeAPI:
            def __init__(self, *args): pass
            def players(self, keys):
                if failure:
                    raise RuntimeError(failure)
                return {key: profile(key, value) for key in keys}
        return FakeAPI

    def test_failed_final_request_keeps_period_and_totals_unchanged(self):
        with patch.object(self.app, 'API', self.fake_api('Нет подключения')):
            self.ui.finish_prompt()
            self.settle()
        self.assertIsNotNone(self.ui.store.running())
        self.assertEqual(self.ui.store.summary()[0]['total'], 0)
        self.assertEqual(len(self.errors), 1)
        self.assertIn('Нет подключения', str(self.errors[0]))
        self.assertEqual(str(self.ui.end_btn['state']), 'normal')
        self.assertEqual(str(self.ui.start_btn['state']), 'disabled')
        self.assertEqual(list((self.root/'reports').glob('*.xlsx')), [])

    def test_failed_excel_after_finish_keeps_results_and_can_export_again(self):
        blocked_folder=self.root/'reports'
        blocked_folder.write_bytes(b'file instead of folder')
        with patch.object(self.app, 'API', self.fake_api()):
            self.ui.finish_prompt()
            self.settle()
        self.assertIsNone(self.ui.store.running())
        self.assertEqual(self.ui.store.summary()[0]['total'], 75)
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(blocked_folder.read_bytes(),b'file instead of folder')
        self.assertIn('Данные уже сохранены', str(self.errors[0]))
        self.assertIn('Период завершён. Excel не сохранён', self.ui.status.get())
        self.assertEqual(str(self.ui.start_btn['state']), 'normal')
        with patch.object(self.app.filedialog, 'askdirectory', return_value=str(self.root)):
            self.ui.save_as()
        path, = self.root.glob('*.xlsx')
        self.assertTrue(path.name.endswith('_period-1_final-copy.xlsx'))
        self.assertEqual(self.ui.store.summary()[0]['total'], 75)
        self.assertEqual(len(self.errors), 1)

    def test_delayed_finish_cannot_close_the_next_period(self):
        entered, release = threading.Event(), threading.Event()
        tag, profile = self.key, self.profile
        class SlowAPI:
            def __init__(self, *args): pass
            def players(self, keys):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('test timeout')
                return {tag: profile(tag, 300)}

        second = self.app.App()
        second.config_data.update(self.ui.config_data)
        try:
            with patch.object(self.app,'API',SlowAPI):
                self.ui.end_btn.invoke()
                self.assertTrue(entered.wait(2))
            with patch.object(self.app,'API',self.fake_api(value=150)):
                second.end_btn.invoke()
                self.settle(second)
            with patch.object(self.app,'API',self.fake_api(value=200)):
                second.start_btn.invoke()
                self.settle(second)
            self.assertEqual(second.store.running()['id'],2)
        finally:
            release.set()
            self.settle()
            second.close()
        self.assertEqual(self.ui.store.running()['id'],2)
        self.assertEqual(self.ui.store.summary()[0]['current'],0)
        self.assertEqual(self.ui.store.summary(1)[0]['current'],50)
        final, = (self.root/'reports').glob('*_final.xlsx')
        self.assertTrue(final.name.endswith('_period-1_final.xlsx'))
        error, = self.errors
        self.assertIn('Период №1 уже завершён',str(error))

    def test_busy_disables_mutations_and_prevents_duplicate_worker(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def work(store):
            calls.append(1)
            entered.set()
            if not release.wait(3):
                raise RuntimeError('test timeout')
            return 'Проверено'
        try:
            self.ui.run(work, 'Проверка')
            self.assertTrue(entered.wait(2))
            self.assertTrue(all(str(button['state']) == 'disabled' for button in self.ui.buttons))
            self.ui.run(work, 'Повтор')
            self.assertEqual(calls, [1])
            self.ui.close()
            self.assertTrue(self.ui.winfo_exists())
        finally:
            release.set()
        self.settle()
        self.assertEqual(self.errors, [])
        self.assertEqual(str(self.ui.end_btn['state']), 'normal')

    def test_backup_contains_committed_current_period_and_is_independent(self):
        from test_core import t
        self.ui.store.recalculate({self.key: self.profile(value=180)}, t(3))
        backup = self.root/'backup.sqlite3'
        self.ui.store.backup(backup)
        self.ui.store.recalculate({self.key: self.profile(value=220)}, t(4))
        with sqlite3.connect(backup) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute('SELECT SUM(last_value-baseline) FROM entries').fetchone()[0], 80)
            self.assertEqual(connection.execute('SELECT count(*) FROM sessions WHERE ended IS NULL').fetchone()[0], 1)
        self.assertEqual(self.ui.store.summary()[0]['total'], 120)


    def test_late_account_shows_cwl_after_period_ends(self):
        from test_core import t
        late = '#Q0Y8Q2L'
        self.ui.store.finish({self.key: self.profile(value=180)}, t(3))
        self.ui.store.add_account(self.pid, self.profile(late, 500), t(4))
        self.ui.store.save_war('#TEST','2026-11',{'state':'warEnded','clan':{
            'tag':'#QYPG','name':'Аркадия','members':[{'tag':late,'attacks':[{'stars':3}]}]}})
        with patch('core.now',return_value=t(5,11)):
            self.ui.refresh()
        self.assertEqual(self.ui.accounts_tree.set(late,'period'),'Не начат')
        self.assertEqual(self.ui.accounts_tree.set(late,'clan'),'Аркадия')
        self.assertEqual(self.ui.accounts_tree.set(late,'stars'),'3')

    @staticmethod
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from DesktopReliability.descendants(child)

    def test_small_settings_keeps_actions_visible_and_scrolls(self):
        self.ui.settings()
        self.ui.update()
        win = next(w for w in self.ui.winfo_children() if w.winfo_class() == 'Toplevel')
        try:
            win.geometry('760x420+0+0')
            self.ui.update()
            controls = {w.cget('text'): w for w in self.descendants(win) if w.winfo_class() == 'TButton'}
            for label in ('Сохранить', 'Выгрузить базу', 'Загрузить базу'):
                button = controls[label]
                self.assertTrue(button.winfo_ismapped(), label)
                self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight(), label)
                self.assertLessEqual(button.winfo_rooty()+button.winfo_height(), win.winfo_rooty()+win.winfo_height(), label)
            canvas = next(w for w in self.descendants(win) if w.winfo_class() == 'Canvas')
            before = canvas.yview()
            self.assertLess(before[1], 1.0)
            canvas.yview_moveto(1)
            self.ui.update()
            self.assertGreater(canvas.yview()[0], before[0])
            self.assertEqual(canvas.yview()[1], 1.0)
            self.assertTrue(controls['Сохранить'].winfo_ismapped())
        finally:
            win.destroy()

    def test_final_file_preserved_by_later_refresh_and_history_copy(self):
        with patch.object(self.app, 'API', self.fake_api()):
            self.ui.finish_prompt()
            self.settle()
            reports = self.root/'reports'
            final, = reports.glob('*.xlsx')
            original = final.read_bytes()
            self.ui.action('refresh')
            self.settle()
            self.assertEqual(list(reports.glob('*.xlsx')), [final])
            self.assertEqual(final.read_bytes(), original)
        self.ui.history_tree.selection_set(str(self.ui.store.latest()['id']))
        with patch.object(self.app.filedialog, 'askdirectory', return_value=str(reports)) as dialog, patch.object(self.app, 'now', return_value=self.app.now()):
            self.ui.export_history()
            copy, = reports.glob('*_final-copy.xlsx')
            first_copy = copy.read_bytes()
            self.ui.export_history()
        self.assertEqual(dialog.call_count,2)
        self.assertEqual(len(list(reports.glob('*_final-copy.xlsx'))),2)
        self.assertEqual(copy.read_bytes(),first_copy)
        self.assertEqual(final.read_bytes(), original)
        self.assertEqual(self.errors, [])

    def test_summary_numeric_sort_and_reverse(self):
        tree = self.ui.summary_tree
        tree.delete(*tree.get_children())
        tree.insert('', 'end', iid='small', values=('А', 9, 9, 9, '#9'))
        tree.insert('', 'end', iid='large', values=('Б', 100, 100, 100, '#100'))
        for column in ('total', 'current', 'best', 'record'):
            self.ui.sort_summary(column)
            self.assertEqual(tree.get_children(), ('large', 'small'))
            self.ui.sort_summary(column)
            self.assertEqual(tree.get_children(), ('small', 'large'))

    def test_restore_player_dialog_preserves_identity_and_history(self):
        from test_core import t
        self.ui.store.archive({self.key: self.profile(value=180)}, [self.key], self.pid, t(3))
        self.ui.refresh()
        self.ui.restore_player()
        self.ui.update()
        win = next(w for w in self.ui.winfo_children() if w.winfo_class() == 'Toplevel')
        choice = next(w for w in self.descendants(win) if w.winfo_class() == 'Listbox')
        self.assertIn(self.key, choice.get(0))
        button = next(w for w in self.descendants(win) if w.winfo_class() == 'TButton' and w.cget('text') == 'Восстановить выбранного')
        button.invoke()
        self.ui.update()
        self.assertEqual(self.ui.store.players()[0]['id'], self.pid)
        self.assertEqual(self.ui.store.accounts(), [])
        self.assertEqual(self.ui.store.summary()[0]['total'], 80)
        self.assertIn('Добавьте его аккаунты', self.ui.status.get())
        self.assertEqual(self.errors, [])

    def test_account_cwl_hint_requires_selection_and_can_be_cleared(self):
        self.ui.set_account_cwl()
        self.assertEqual(len(self.errors), 1)
        self.assertIn('Выберите аккаунт', str(self.errors[0]))
        self.errors.clear()
        self.ui.accounts_tree.selection_set(self.key)
        with patch.object(self.app.simpledialog, 'askstring', return_value='#QYPG'):
            self.ui.set_account_cwl()
        self.assertEqual(self.ui.store.get_cwl_clan(self.key), '#QYPG')
        with patch.object(self.app.simpledialog, 'askstring', return_value=''):
            self.ui.set_account_cwl()
        self.assertIsNone(self.ui.store.get_cwl_clan(self.key))
        self.assertEqual(self.errors, [])

    def test_small_main_window_header_actions_and_restore_fit(self):
        self.ui.geometry('980x480+0+0')
        for tab in (0, 1):
            self.ui.notebook.select(tab)
            self.ui.update()
            expected = ('Настройки', 'Открыть отчёты', 'Сохранить Excel', '↻ Пересчитать всё', '■ Завершить и сохранить Excel')
            if tab == 0:
                expected += ('+ Добавить аккаунт по тегу', 'Удалить аккаунт', 'Клан ЛВК')
            else:
                expected += ('Восстановить игрока из архива',)
            buttons = {b.cget('text'): b for b in self.ui.buttons}
            if tab == 0:
                row = self.ui.accounts_tree.bbox(self.key)
                self.assertTrue(row, 'first account row must be visible')
                self.assertLessEqual(row[1]+row[3], self.ui.accounts_tree.winfo_height(), 'first account row must fit completely')
            for label in expected:
                button = buttons[label]
                self.assertTrue(button.winfo_ismapped(), label)
                self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth(), label)
                self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight(), label)
                self.assertLessEqual(button.winfo_rootx()+button.winfo_width(), self.ui.winfo_rootx()+self.ui.winfo_width(), label)
                self.assertLessEqual(button.winfo_rooty()+button.winfo_height(), self.ui.winfo_rooty()+self.ui.winfo_height(), label)


if __name__ == '__main__':
    unittest.main(verbosity=2)
