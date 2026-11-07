"""Optional GUI smoke tests; run with a graphical desktop or xvfb-run."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

@unittest.skipUnless(os.environ.get('DISPLAY') or os.name=='nt','A graphical display is needed')
class DesktopWorkflow(unittest.TestCase):
    def test_buttons_keep_full_height_in_short_windows(self):
        import app
        from tkinter import ttk

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(app, DATA=root, DB=root/'test.db', CONFIG=root/'settings.json'):
                ui = app.App()
                try:
                    buttons = {button.cget('text'): button for button in ui.buttons}
                    style = ttk.Style(ui)
                    for width, height, row_height, padding in ((1100, 480, 34, 9), (1366, 650, 44, 13)):
                        with self.subTest(width=width, height=height):
                            style.configure('Treeview', rowheight=row_height)
                            style.configure('TButton', padding=(12, padding))
                            ui.geometry(f'{width}x{height}+0+0')
                            for tab, labels in (
                                (0, ('Добавить игрока', 'Изменить имя', 'Удалить игрока',
                                     '+ Добавить аккаунт по тегу', 'Удалить аккаунт')),
                                (2, ('Сохранить Excel выбранного периода',)),
                            ):
                                ui.notebook.select(tab)
                                ui.update()
                                for label in labels:
                                    button = buttons[label]
                                    self.assertTrue(button.winfo_ismapped(), label)
                                    self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight(), label)
                                    self.assertLessEqual(
                                        button.winfo_rooty() + button.winfo_height(),
                                        ui.winfo_rooty() + ui.winfo_height(), label,
                                    )
                                self.assertTrue(ui.progress.winfo_ismapped())
                finally:
                    ui.close()

    def test_window_crud_start_refresh_finish_settings_and_restart(self):
        import app
        from openpyxl import load_workbook
        from test_core import profile
        values={'#P0Y8Q2L':100,'#Q0Y8Q2L':500}
        requests=[]
        class FakeAPI:
            def __init__(self,*args): pass
            def players(self,keys):
                requests.append(keys)
                return {k:profile(k,values[k]) for k in keys}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); errors=[]
            reports=root/'reports'
            (root/'settings.json').write_text('{"auto": true}',encoding='utf-8')
            with patch.multiple(app,DATA=root,DB=root/'test.db',CONFIG=root/'settings.json'),patch.object(app,'API',FakeAPI),patch.object(app,'sync_cwl',return_value=[]),patch.object(app.messagebox,'showerror',side_effect=lambda *a,**kw:errors.append(a)),patch.object(app.messagebox,'showinfo'),patch.object(app.messagebox,'askyesno',return_value=True):
                ui=app.App(); ui.config_data.update(reports=str(reports),token='test')
                def settle():
                    deadline=time.monotonic()+5
                    while ui.busy and time.monotonic()<deadline:
                        ui.update(); time.sleep(.01)
                    ui.update(); self.assertFalse(ui.busy); self.assertEqual(errors,[])
                with patch.object(app.simpledialog,'askstring',return_value='Игрок'): ui.add_player()
                with patch.object(app.simpledialog,'askstring',return_value='#P0Y8Q2L'): ui.add_account()
                settle()
                ui.action('start'); settle(); self.assertIsNotNone(ui.store.running())
                self.assertEqual(str(ui.start_btn['state']),'disabled')
                with patch.object(app.simpledialog,'askstring',return_value='#Q0Y8Q2L'): ui.add_account()
                settle()
                self.assertEqual(ui.accounts_tree.set('#Q0Y8Q2L','friend'),'500')
                self.assertEqual(ui.accounts_tree.set('#Q0Y8Q2L','period'),'0')
                self.assertEqual(list(reports.glob('*.xlsx')),[])
                request_count=len(requests)
                ui.close(); ui=app.App(); ui.update()
                ui.config_data.update(reports=str(reports),token='test')
                self.assertIsNotNone(ui.store.running())
                self.assertNotIn('auto',ui.config_data)
                self.assertEqual(ui.tk.call('after','info'),(ui.poll_job,))
                self.assertEqual(len(requests),request_count)
                values['#P0Y8Q2L']+=10; values['#Q0Y8Q2L']+=20
                ui.action('refresh'); settle(); self.assertEqual(ui.store.summary()[0]['total'],30)
                ui.action('refresh'); settle(); self.assertEqual(ui.store.summary()[0]['total'],30)
                self.assertEqual(list(reports.glob('*.xlsx')),[])
                request_count=len(requests)
                def select_reports_folder(**kwargs):
                    self.assertEqual(Path(kwargs['initialdir']),reports)
                    return str(reports)
                with patch.object(app.filedialog,'askdirectory',side_effect=select_reports_folder):
                    ui.save_as()
                self.assertEqual(len(requests),request_count)
                self.assertIsNotNone(ui.store.running())
                snapshot,=reports.glob('*.xlsx')
                self.assertTrue(snapshot.name.endswith('_period-1_snapshot.xlsx'))
                with snapshot.open('rb') as file:
                    workbook=load_workbook(file,data_only=True)
                    self.assertEqual(workbook['Итоги по игрокам']['C5'].value,30)
                    self.assertEqual([workbook['Аккаунты'][f'D{i}'].value for i in (5,6)],[10,20])
                    workbook.close()
                with patch.object(app.simpledialog,'askstring',return_value='Новое имя'): ui.rename()
                self.assertEqual(ui.store.players()[0]['name'],'Новое имя')
                ui.accounts_tree.selection_set('#Q0Y8Q2L'); ui.remove_account(); settle()
                self.assertEqual(len(ui.store.accounts()),1)
                self.assertEqual(list(reports.glob('*.xlsx')),[snapshot])
                ui.finish_prompt(); settle(); self.assertIsNone(ui.store.running())
                self.assertEqual(ui.store.summary()[0]['total'],30)
                final,=set(reports.glob('*.xlsx'))-{snapshot}
                self.assertTrue(final.name.endswith('_period-1_final.xlsx'))
                ui.settings(); ui.update()
                windows=[w for w in ui.winfo_children() if w.winfo_class()=='Toplevel']
                self.assertEqual(len(windows),1)
                windows[0].destroy(); ui.update(); ui.close()
                ui=app.App(); ui.update()
                self.assertEqual(ui.store.summary()[0]['total'],30)
                self.assertIsNone(ui.store.running()); ui.close()

if __name__=='__main__': unittest.main(verbosity=2)
