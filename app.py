import datetime as dt
from itertools import count
import json
import os
from pathlib import Path
import queue
import sqlite3
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from core import Store, API, tag, sync_cwl, now, MSK
from excel_export import export

DATA=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'ClashTracker'
DATA.mkdir(parents=True,exist_ok=True)
CONFIG=DATA/'settings.json'
DB=DATA/'tracker.sqlite3'
DEFAULT={'token':'','route':'royaleapi','reports':str(Path.home()/'Documents'/'ClashTracker')}

class FinalReportError(RuntimeError):
    pass

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Clash Tracker 0.9 · Донат и ЛВК')
        self.minsize(980, 480)
        height = min(780, max(480, self.winfo_screenheight() - 100))
        self.geometry(f'1200x{height}')
        self.configure(bg='#edf2f6'); self.config_data=DEFAULT.copy()
        try: self.config_data.update(json.loads(CONFIG.read_text(encoding='utf-8')))
        except (OSError,ValueError): pass
        self.config_data.pop('auto', None)
        self.store=Store(DB); self.jobs=queue.Queue(); self.busy=False; self.buttons=[]
        style=ttk.Style(self); style.theme_use('clam')
        style.configure('TFrame',background='#edf2f6'); style.configure('TLabel',background='#edf2f6',font=('Segoe UI',10))
        style.configure('TButton',font=('Segoe UI',10),padding=(12,9))
        style.configure('Accent.TButton',background='#176b78',foreground='white')
        style.map('Accent.TButton',background=[('active','#125460')])
        style.configure('Treeview',font=('Segoe UI',10),rowheight=34,background='white',fieldbackground='white')
        style.configure('Treeview.Heading',font=('Segoe UI',10,'bold'),padding=8)
        frame=ttk.Frame(self,padding=12); frame.pack(fill='both',expand=True)
        head=ttk.Frame(frame); head.pack(fill='x')
        ttk.Label(head,text='CLASH TRACKER',font=('Segoe UI',23,'bold'),foreground='#163550').pack(side='left')
        self.button(head,'Настройки',self.settings).pack(side='right')
        self.button(head,'Открыть отчёты',self.open_reports).pack(side='right',padx=8)
        self.state_text=tk.StringVar(); ttk.Label(frame,textvariable=self.state_text,font=('Segoe UI',11)).pack(anchor='w',pady=(4,6))
        bar=ttk.Frame(frame); bar.pack(fill='x',pady=(0,6))
        self.start_btn=self.button(bar,'▶ Начать подсчёт для всех',lambda:self.action('start'),'Accent.TButton'); self.start_btn.pack(side='left',padx=(0,8))
        self.button(bar,'↻ Пересчитать всё',lambda:self.action('refresh')).pack(side='left',padx=(0,8))
        self.end_btn=self.button(bar,'■ Завершить и сохранить Excel',self.finish_prompt); self.end_btn.pack(side='left',padx=(0,8))
        self.button(head,'Сохранить Excel',self.save_as).pack(side='right')
        self.notebook=ttk.Notebook(frame)
        page=ttk.Frame(self.notebook,padding=12); self.notebook.add(page,text='  Игроки и аккаунты  ')
        left=ttk.Frame(page); left.pack(side='left',fill='y',padx=(0,14))
        ttk.Label(left,text='Игроки',font=('Segoe UI',12,'bold')).pack(anchor='w',pady=(0,8))
        player_actions=ttk.Frame(left); player_actions.pack(side='bottom',fill='x')
        for title,fn in [('Добавить игрока',self.add_player),('Изменить имя',self.rename),('Удалить игрока',self.remove_player)]:
            self.button(player_actions,title,fn).pack(fill='x',pady=(6,0))
        self.player_list=tk.Listbox(left,width=24,font=('Segoe UI',12),height=12,exportselection=False,bd=0,highlightthickness=1,highlightbackground='#cbd7df',selectbackground='#176b78')
        self.player_list.pack(fill='both',expand=True); self.player_list.bind('<<ListboxSelect>>',lambda e:self.show_accounts())
        right=ttk.Frame(page); right.pack(side='left',fill='both',expand=True)
        ttk.Label(right,text='Аккаунты выбранного игрока',font=('Segoe UI',12,'bold')).pack(anchor='w',pady=(0,8))
        # Reserve room for controls before the table takes the remaining height.
        account_footer=ttk.Frame(right); account_footer.pack(side='bottom',fill='x',pady=(8,0))
        actions=ttk.Frame(account_footer); actions.pack(fill='x')
        self.button(actions,'+ Добавить аккаунт по тегу',self.add_account).pack(side='left',padx=(0,8))
        self.button(actions,'Удалить аккаунт',self.remove_account).pack(side='left')
        self.button(actions,'Клан ЛВК',self.set_account_cwl).pack(side='left',padx=(8,0))
        ttk.Label(account_footer,text='ЛВК: текущий сезон, по полученным войнам. Удаление сохраняет вклад.',wraplength=560).pack(anchor='w',pady=(4,0))
        self.accounts_tree=self.tree(right,[('nick','Ник',150),('tag','Тег',115),('friend','Настоящий друг',130),('period','Донат за период',130),('clan','Клан ЛВК',155),('stars','Звёзды',80)])
        summary=ttk.Frame(self.notebook,padding=12); self.notebook.add(summary,text='  Итоги и рекорды  ')
        summary_footer=ttk.Frame(summary); summary_footer.pack(side='bottom',fill='x')
        self.button(summary_footer,'Восстановить игрока из архива',self.restore_player).pack(side='right')
        summary_note = ttk.Label(summary_footer,text='Все аккаунты игрока. Паузы между периодами не учитываются.\nРекорд — среди завершённых периодов. Нажмите заголовок для сортировки.',wraplength=600)
        summary_note.pack(side='bottom',anchor='w',pady=(8,0))
        self.summary_tree=self.tree(summary,[('name','Игрок',190),('total','Донат за всё время',165),('current','Донат за период',155),('best','Лучший период: донат',175),('record','Период рекорда',145)])
        for column in ('name','total','current','best','record'):
            self.summary_tree.heading(column,command=lambda c=column:self.sort_summary(c))
        history=ttk.Frame(self.notebook,padding=12); self.notebook.add(history,text='  История периодов  ')
        history_footer=ttk.Frame(history); history_footer.pack(side='bottom',fill='x')
        self.button(history_footer,'Сохранить Excel выбранного периода',self.export_history).pack(anchor='w',pady=8)
        ttk.Label(history_footer,text='В старом периоде донат зафиксирован. ЛВК в выгрузке показывает текущий сезон; общие итоги — на дату выгрузки.',wraplength=1050).pack(anchor='w')
        self.history_tree=self.tree(history,[('id','№',70),('start','Начало МСК',220),('end','Завершение МСК',220),('total','Донат',180)])
        logpage=ttk.Frame(self.notebook,padding=12); self.notebook.add(logpage,text='  Журнал обновлений  ')
        self.logbox=tk.Text(logpage,height=12,font=('Consolas',10),state='disabled',wrap='word'); self.logbox.pack(fill='both',expand=True)
        foot=ttk.Frame(frame); foot.pack(side='bottom',fill='x',pady=(10,0))
        status='Готово. Добавьте игроков и аккаунты.'
        if not self.config_data['token'].strip():
            status='Чтобы получать данные игры, откройте «Настройки» и добавьте свой ключ Supercell API.'
        self.status=tk.StringVar(value=status)
        ttk.Label(foot,textvariable=self.status,wraplength=1120).pack(anchor='w')
        self.progress=ttk.Progressbar(foot,mode='indeterminate'); self.progress.pack(fill='x',pady=(6,0))
        self.notebook.pack(fill='both',expand=True)
        self.refresh(); self.poll_job=self.after(100,self.poll)
        self.protocol('WM_DELETE_WINDOW',self.close)
    def button(self,parent,text,command,style='TButton'):
        b=ttk.Button(parent,text=text,command=command,style=style); self.buttons.append(b); return b
    def tree(self,parent,columns):
        frame=ttk.Frame(parent); frame.pack(fill='both',expand=True)
        tree=ttk.Treeview(frame,columns=[c[0] for c in columns],show='headings',selectmode='browse')
        for key,title,width in columns: tree.heading(key,text=title); tree.column(key,width=width,minwidth=70)
        y=ttk.Scrollbar(frame,orient='vertical',command=tree.yview); x=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview)
        tree.configure(yscrollcommand=y.set,xscrollcommand=x.set); tree.grid(row=0,column=0,sticky='nsew'); y.grid(row=0,column=1,sticky='ns'); x.grid(row=1,column=0,sticky='ew')
        frame.rowconfigure(0,weight=1); frame.columnconfigure(0,weight=1); return tree
    def log(self,text):
        self.logbox.configure(state='normal'); self.logbox.insert('end',dt.datetime.now().strftime('%H:%M:%S')+'  '+text+'\n'); self.logbox.see('end'); self.logbox.configure(state='disabled')
    def selected_player(self):
        selection=self.player_list.curselection()
        if not selection: raise ValueError('Выберите игрока слева.')
        return self.player_rows[selection[0]]
    def refresh(self):
        selected=None
        try: selected=self.selected_player()['id']
        except ValueError: pass
        self.player_rows=self.store.players(); self.player_list.delete(0,'end')
        for i,p in enumerate(self.player_rows):
            self.player_list.insert('end',p['name'])
            if p['id']==selected: self.player_list.selection_set(i)
        if self.player_rows and not self.player_list.curselection(): self.player_list.selection_set(0)
        self.show_accounts()
        active=self.store.running(); latest=self.store.latest()
        self.active_period_id=active['id'] if active else None
        self.summary_tree.heading('current',text=f'Донат за период #{latest["id"]}' if latest else 'Донат за период')
        self.summary_tree.delete(*self.summary_tree.get_children())
        for p in self.store.summary():
            record = f'#{p["best_period"]}' if p['best_period'] is not None else '—'
            self.summary_tree.insert('','end',values=(p['name']+(' [архив]' if not p['active'] else ''),p['total'],p['current'],p['best'],record))
        if hasattr(self,'sort_column'): self.sort_summary(self.sort_column,self.sort_desc)
        self.history_tree.delete(*self.history_tree.get_children())
        for s in self.store.db.execute('SELECT * FROM sessions ORDER BY id DESC'):
            fmt=lambda t:dt.datetime.fromisoformat(t).astimezone(MSK).strftime('%d.%m.%Y %H:%M') if t else 'Идёт'
            total=self.store.db.execute('SELECT COALESCE(SUM(last_value-baseline),0) FROM entries WHERE session=?',(s['id'],)).fetchone()[0]
            self.history_tree.insert('','end',iid=str(s['id']),values=(s['id'],fmt(s['started']),fmt(s['ended']),total))
        state='Подсчёт идёт · период #'+str(active['id']) if active else 'Подсчёт завершён · период #'+str(latest['id']) if latest else 'Подсчёт ещё не начат'
        if latest:
            started=dt.datetime.fromisoformat(latest['started']).astimezone(MSK).strftime('%d.%m.%Y %H:%M')
            state+=f' · с {started} МСК'
        self.state_text.set(state+f' · игроков {len(self.player_rows)}, аккаунтов {len(self.store.accounts())}')
        self.start_btn.configure(text='▶ Начать следующий период' if latest else '▶ Начать подсчёт для всех')
        if not self.busy:
            self.start_btn.configure(state='disabled' if active else 'normal'); self.end_btn.configure(state='normal' if active else 'disabled')
    def show_accounts(self):
        self.accounts_tree.delete(*self.accounts_tree.get_children())
        try: player=self.selected_player()['id']
        except ValueError: return
        stats={r['tag']:r for r in self.store.rows()}
        for a in self.store.accounts():
            if a['player']!=player: continue
            r=stats.get(a['tag'],{})
            self.accounts_tree.insert('','end',iid=a['tag'],values=(a['nick'],a['tag'],a['friend'],r.get('total',0),r.get('cwl_clan','Нет данных'),r.get('stars') if r.get('stars') is not None else 'Нет данных'))
    def attempt(self,fn):
        try: fn()
        except Exception as exc: messagebox.showerror('Clash Tracker',str(exc),parent=self)
    def run(self,work,message,done=None):
        if self.busy: return
        self.busy=True; self.status.set(message); self.progress.start(12)
        for b in self.buttons: b.configure(state='disabled')
        def worker():
            store=None
            try:
                store=Store(DB); result=work(store)
                self.jobs.put((True,result,done))
            except Exception as exc: self.jobs.put((False,exc,None))
            finally:
                if store: store.db.close()
        threading.Thread(target=worker,daemon=True).start()
    def poll(self):
        try:
            ok,result,done=self.jobs.get_nowait(); self.busy=False; self.progress.stop()
            for b in self.buttons: b.configure(state='normal')
            self.refresh()
            if ok:
                self.status.set(str(result).split('\n')[0]); self.log(str(result))
                if done: done(result)
            else:
                if isinstance(result,FinalReportError):
                    self.status.set('Период завершён. Excel не сохранён. Повторите выгрузку из истории периодов.')
                else:
                    self.status.set('Операция не завершена. Подробности в журнале.')
                self.log(str(result))
                messagebox.showerror('Clash Tracker',str(result),parent=self)
        except queue.Empty: pass
        self.poll_job=self.after(100,self.poll)
    def save_report(self,store,folder,sid,phase):
        stamp=now().astimezone(MSK).strftime('%Y-%m-%d_%H-%M-%S_%f')
        period=f'_period-{sid}' if sid is not None else ''
        prefix=f'Clash_{stamp}{period}'
        for number in count(1):
            sequence=f'_{number}' if number>1 else ''
            path=folder/f'{prefix}{sequence}_{phase}.xlsx'
            if os.path.lexists(path):
                continue
            try:
                return export(store,path,sid)
            except FileExistsError:
                if not os.path.lexists(path):
                    raise
    def save_final_report(self,store,sid):
        try:
            return self.save_report(store,Path(self.config_data['reports']),sid,'final')
        except Exception as exc:
            raise FinalReportError(f'Период №{sid} завершён. Данные уже сохранены в базе, но Excel записать не удалось. Нажмите «Сохранить Excel» или выберите этот период в истории.\n{exc}') from None
    def action(self,mode,*,period_id=None):
        def begin():
            if mode=='finish' and period_id is None: raise ValueError('Нет активного подсчёта.')
            api=API(self.config_data['token'],self.config_data['route'])
            if mode=='start' and self.store.running(): raise ValueError('Подсчёт уже идёт.')
            if mode=='start' and not self.store.accounts(): raise ValueError('Сначала добавьте аккаунты.')
            def work(store):
                profiles=api.players([a['tag'] for a in store.accounts()])
                sid=None
                if mode=='start': sid=store.start(profiles)
                elif mode=='finish': sid=store.finish(profiles,expected_sid=period_id)
                else: store.recalculate(profiles)
                try:
                    warnings=sync_cwl(store,api)
                except Exception as exc:
                    warnings=[str(exc)]
                total=sum(p['current'] for p in store.summary(sid))
                title=('Подсчёт начат.' if mode=='start' else 'Период завершён.' if mode=='finish' else 'Донат обновлён.')
                title+=(' ЛВК обновлена частично — смотрите журнал.' if warnings else ' Запросы ЛВК выполнены; звёзды по доступным войнам.')
                result=title+f'\nИтог периода: {total}. Обновлено: '+now().astimezone(MSK).strftime('%d.%m.%Y %H:%M МСК')
                if mode=='finish': result+=f'\nExcel: {self.save_final_report(store,sid)}'
                if warnings: result+='\nЛВК: часть данных недоступна.\n'+'\n'.join(warnings)
                return result
            message='Запрашиваю все аккаунты параллельно; затем обновляю ЛВК'
            if mode=='finish': message+=' и сохраняю итоговый Excel'
            self.run(work,message+'…',lambda result:messagebox.showinfo('Готово',result,parent=self) if mode in ('start','finish') else None)
        self.attempt(begin)
    def finish_prompt(self):
        sid=self.active_period_id
        if sid is not None and messagebox.askyesno('Завершить подсчёт?',f'Получить конечные значения, закрыть период №{sid} и сохранить один общий отчёт Excel?\n\nВклад удалённых аккаунтов сохранится. Новый период начнётся по кнопке «Начать следующий период».',parent=self):
            self.action('finish',period_id=sid)
    def add_player(self):
        name=simpledialog.askstring('Новый игрок','Имя игрока:',parent=self)
        if name is not None: self.attempt(lambda:(self.store.add_player(name),self.refresh()))
    def rename(self):
        def work():
            p=self.selected_player(); name=simpledialog.askstring('Изменить имя','Имя игрока:',initialvalue=p['name'],parent=self)
            if name is not None: self.store.rename(p['id'],name); self.refresh()
        self.attempt(work)
    def add_account(self):
        def begin():
            p=self.selected_player(); key=simpledialog.askstring('Добавить аккаунт','Тег аккаунта из профиля Clash of Clans:',parent=self)
            if key is None: return
            key=tag(key); api=API(self.config_data['token'],self.config_data['route'])
            def work(store):
                profile=api.players([key])[key]; store.add_account(p['id'],profile)
                baseline=store.db.execute('SELECT friend FROM accounts WHERE tag=?',(key,)).fetchone()[0]
                result=f'Аккаунт {profile["name"]} ({key}) добавлен. «Настоящий друг»: {baseline}.'
                if store.running(): result+=' Донат в текущем периоде считается с этого значения.'
                return result
            self.run(work,'Получаю профиль и исходное значение «Настоящий друг»…')
        self.attempt(begin)
    def remove_player(self):
        def begin():
            p=self.selected_player(); keys=[a['tag'] for a in self.store.accounts() if a['player']==p['id']]
            self.archive(keys,p['id'],p['name'])
        self.attempt(begin)
    def remove_account(self):
        def begin():
            selected=self.accounts_tree.selection()
            if not selected: raise ValueError('Выберите аккаунт в таблице.')
            self.archive([selected[0]],None,selected[0])
        self.attempt(begin)
    def archive(self,keys,player,name):
        if not messagebox.askyesno('Удалить из списка?',name+' будет скрыт из списка. Накопленная статистика останется в истории. Продолжить?',parent=self): return
        api=API(self.config_data['token'],self.config_data['route']) if keys and self.store.running() else None
        def work(store):
            profiles=api.players(keys) if api else {}
            store.archive(profiles,keys,player)
            return 'Удалено из активного списка: '+name
        self.run(work,'Получаю конечные значения и сохраняю историю…')
    def save_as(self, session=None):
        session=self.store.latest() if session is None else session
        phase='final-copy' if session and session['ended'] else 'snapshot'
        sid=session['id'] if session else None
        folder=Path(self.config_data['reports'])
        try: folder.mkdir(parents=True,exist_ok=True)
        except OSError: folder=Path.home()
        selected=filedialog.askdirectory(parent=self,title='Выберите папку для нового отчёта Excel',initialdir=str(folder))
        if selected:
            def save():
                path=self.save_report(self.store,Path(selected),sid,phase)
                self.log(f'Excel сохранён: {path}')
                self.status.set(f'Excel сохранён: {path}')
            self.attempt(save)
    def export_history(self):
        selection=self.history_tree.selection()
        if not selection: messagebox.showinfo('Период','Выберите период в таблице.',parent=self); return
        session=self.store.db.execute('SELECT * FROM sessions WHERE id=?',(int(selection[0]),)).fetchone()
        if session: self.save_as(session)
    def sort_summary(self,column,descending=None):
        if descending is None:
            descending = not getattr(self,'sort_desc',False) if getattr(self,'sort_column',None)==column else column!='name'
        def value(iid):
            cell=self.summary_tree.set(iid,column)
            if column=='name': return cell.casefold()
            return int(cell.lstrip('#')) if cell.lstrip('#').isdigit() else -1
        items=sorted(self.summary_tree.get_children(),key=value,reverse=descending)
        for i,iid in enumerate(items): self.summary_tree.move(iid,'',i)
        self.sort_column=column; self.sort_desc=descending
    def open_reports(self):
        def work():
            folder=Path(self.config_data['reports']); folder.mkdir(parents=True,exist_ok=True)
            if os.name=='nt': os.startfile(str(folder))
            else: messagebox.showinfo('Папка отчётов',str(folder),parent=self)
        self.attempt(work)
    def restore_player(self):
        players=self.store.archived_players()
        if not players:
            messagebox.showinfo('Архив','Нет удалённых игроков.',parent=self); return
        win=tk.Toplevel(self); win.title('Восстановить игрока'); win.geometry('560x350'); win.transient(self); win.grab_set()
        frame=ttk.Frame(win,padding=16); frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Вернётся прежний игрок с его историей. Аккаунты добавьте заново по тегам — их учёт продолжится с нового запроса.',wraplength=510).pack(fill='x')
        choices=tk.Listbox(frame,exportselection=False,font=('Segoe UI',11)); choices.pack(fill='both',expand=True,pady=10)
        for player in players:
            keys=[r[0] for r in self.store.db.execute('SELECT tag FROM accounts WHERE player=?',(player['id'],))]
            choices.insert('end',player['name']+' · '+', '.join(keys))
        choices.selection_set(0)
        def restore():
            def work():
                selected=choices.curselection()
                if not selected: raise ValueError('Выберите игрока.')
                self.store.restore_player(players[selected[0]]['id']); self.refresh(); win.destroy()
                self.status.set('Игрок восстановлен. Добавьте его аккаунты по тегам для продолжения учёта.')
            self.attempt(work)
        ttk.Button(frame,text='Восстановить выбранного',command=restore).pack(fill='x')
    def set_account_cwl(self):
        def work():
            selected=self.accounts_tree.selection()
            if not selected: raise ValueError('Выберите аккаунт в таблице.')
            key=selected[0]
            value=simpledialog.askstring('Клан ЛВК выбранного аккаунта','Тег клана ЛВК в текущем сезоне. Пустое поле убирает привязку.\nЭто подсказка для поиска: участие и звёзды подтверждаются API.',initialvalue=self.store.get_cwl_clan(key) or '',parent=self)
            if value is None: return
            self.store.set_cwl_clan(key,tag(value) if value.strip() else None)
            self.status.set('Клан ЛВК сохранён. Нажмите «Пересчитать всё», чтобы получить данные.')
        self.attempt(work)
    def settings(self):
        win=tk.Toplevel(self); win.title('Настройки'); win.geometry(f'760x{min(650,max(420,self.winfo_screenheight()-100))}'); win.minsize(640,420); win.transient(self); win.grab_set()
        shell=ttk.Frame(win,padding=12); shell.pack(fill='both',expand=True)
        buttons=ttk.Frame(shell); buttons.pack(side='bottom',fill='x',pady=(10,0))
        canvas=tk.Canvas(shell,highlightthickness=0,background='#edf2f6')
        scroll=ttk.Scrollbar(shell,orient='vertical',command=canvas.yview); scroll.pack(side='right',fill='y')
        canvas.pack(side='left',fill='both',expand=True); canvas.configure(yscrollcommand=scroll.set)
        frame=ttk.Frame(canvas,padding=8); content=canvas.create_window((0,0),window=frame,anchor='nw')
        frame.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(content,width=e.width))
        ttk.Label(frame,text='Ключ разработчика Supercell API',font=('Segoe UI',12,'bold')).pack(anchor='w')
        token=tk.StringVar(value=self.config_data['token']); entry=ttk.Entry(frame,textvariable=token,show='•'); entry.pack(fill='x',pady=8)
        show=tk.BooleanVar(); ttk.Checkbutton(frame,text='Показать ключ',variable=show,command=lambda:entry.configure(show='' if show.get() else '•')).pack(anchor='w')
        route=tk.StringVar(value=self.config_data['route'])
        ttk.Radiobutton(frame,text='RoyaleAPI Proxy — в ключе разрешён IP 45.79.218.79',variable=route,value='royaleapi').pack(anchor='w',pady=(8,0))
        ttk.Radiobutton(frame,text='Напрямую Supercell — в ключе разрешён внешний IP компьютера',variable=route,value='direct').pack(anchor='w')
        ttk.Label(frame,text='Нужен ключ разработчика с developer.clashofclans.com.\nЧерез RoyaleAPI запросы и ключ передаются этому прокси по HTTPS.\nКлюч сохраняется локально в settings.json; в резервную копию базы не входит.',wraplength=710).pack(anchor='w',pady=(4,6))
        ttk.Label(frame,text='Папка отчётов Excel').pack(anchor='w'); report=tk.StringVar(value=self.config_data['reports'])
        folder=ttk.Frame(frame); folder.pack(fill='x',pady=6); ttk.Entry(folder,textvariable=report).pack(side='left',fill='x',expand=True)
        ttk.Button(folder,text='Выбрать',command=lambda:report.set(filedialog.askdirectory(parent=win) or report.get())).pack(side='right')
        ttk.Label(frame,text='Кланы ЛВК: теги через пробел или запятую').pack(anchor='w',pady=(10,0))
        clans=tk.StringVar(value=' '.join(c[0] for c in self.store.db.execute('SELECT tag FROM clans'))); ttk.Entry(frame,textvariable=clans).pack(fill='x',pady=6)
        ttk.Label(frame,text='Укажите также кланы, где игроки выступали до перехода. Текущие кланы аккаунтов проверяются автоматически.',wraplength=670).pack(anchor='w')
        ttk.Label(frame,text='Данные и резервные копии: '+str(DATA),wraplength=670).pack(anchor='w',pady=4)
        def save():
            try:
                values=[tag(v) for v in clans.get().replace(',',' ').split()]
                if not report.get().strip(): raise ValueError('Выберите папку отчётов.')
                self.config_data.update(token=token.get().strip(),route=route.get(),reports=report.get().strip())
                CONFIG.write_text(json.dumps(self.config_data,ensure_ascii=False,indent=2),encoding='utf-8')
                with self.store.db:
                    self.store.db.execute('DELETE FROM clans')
                    self.store.db.executemany('INSERT OR IGNORE INTO clans VALUES(?)',[(v,) for v in values])
                win.destroy(); self.log('Настройки сохранены.')
            except Exception as exc: messagebox.showerror('Настройки',str(exc),parent=win)
        ttk.Button(buttons,text='Сохранить',command=save,style='Accent.TButton').pack(side='right')
        db_filetypes=[('База SQLite','*.sqlite3 *.sqlite *.db'),('Все файлы','*')]
        def backup():
            path=filedialog.asksaveasfilename(parent=win,title='Выгрузить базу',
                initialfile='Clash_backup_'+now().astimezone(MSK).strftime('%Y-%m-%d_%H-%M-%S')+'.sqlite3',
                defaultextension='.sqlite3',filetypes=db_filetypes)
            if not path: return
            try:
                self.store.backup(path)
                messagebox.showinfo('База выгружена','Файл для переноса:\n'+path,parent=win)
            except (OSError,ValueError,sqlite3.Error) as exc:
                messagebox.showerror('Выгрузка базы',str(exc),parent=win)
        def load_backup():
            path=filedialog.askopenfilename(parent=win,title='Загрузить базу',filetypes=db_filetypes)
            if not path or not messagebox.askyesno('Загрузить базу',
                'Данные в приложении будут заменены данными из файла. Текущая база будет сохранена отдельной копией. Продолжить?',
                parent=win): return
            try:
                previous=self.store.import_backup(path)
            except (OSError,ValueError,sqlite3.Error) as exc:
                messagebox.showerror('Загрузка базы',str(exc),parent=win)
                return
            clans.set(' '.join(c[0] for c in self.store.db.execute('SELECT tag FROM clans')))
            self.refresh()
            self.status.set('База загружена.')
            self.log(f'База загружена. Предыдущая база: {previous}')
            messagebox.showinfo('База загружена',f'Предыдущая база сохранена:\n{previous}',parent=win)
        ttk.Button(buttons,text='Выгрузить базу',command=backup).pack(side='left')
        ttk.Button(buttons,text='Загрузить базу',command=load_backup).pack(side='left',padx=(8,0))
    def close(self):
        if self.busy:
            messagebox.showinfo('Обновление','Дождитесь окончания запроса перед закрытием.',parent=self); return
        self.after_cancel(self.poll_job)
        self.store.db.close(); self.destroy()

if __name__=='__main__':
    App().mainloop()
