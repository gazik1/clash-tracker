"""Local accounting. Standard-library-only, read-only API client."""
import datetime as dt
import json
from pathlib import Path
import re
import sqlite3
import socket
import urllib.request
import urllib.error
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed
UTC=dt.timezone.utc
MSK=dt.timezone(dt.timedelta(hours=3))
def now(): return dt.datetime.now(UTC)
def tag(value):
    value='#'+value.strip().upper().lstrip('#')
    if not re.fullmatch(r'#[0289PYLQGRJCUV]{3,15}',value):
        raise ValueError('Неверный тег. Скопируйте его из профиля игры, например #P0Y8Q2L.')
    return value

def friend(profile):
    for a in profile.get('achievements',[]):
        if a.get('name')=='Friend in Need' and a.get('village','home')=='home':
            value=int(a['value'])
            if value>=0: return value
    raise ValueError('API не вернул достижение «Настоящий друг» (Friend in Need). Подсчёт не изменён.')

class Store:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path); self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS players(id INTEGER PRIMARY KEY,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS accounts(tag TEXT PRIMARY KEY,player INTEGER NOT NULL REFERENCES players(id),
          nick TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,friend INTEGER NOT NULL,last_time TEXT NOT NULL,
          clan_tag TEXT,clan_name TEXT);
        CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY,started TEXT NOT NULL,ended TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_running ON sessions((1)) WHERE ended IS NULL;
        CREATE TABLE IF NOT EXISTS entries(session INTEGER REFERENCES sessions(id),tag TEXT REFERENCES accounts(tag),
          player INTEGER REFERENCES players(id),baseline INTEGER NOT NULL,last_value INTEGER NOT NULL,
          started TEXT NOT NULL,ended TEXT,PRIMARY KEY(session,tag));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,session INTEGER REFERENCES sessions(id),
          tag TEXT REFERENCES accounts(tag),player INTEGER REFERENCES players(id),time TEXT NOT NULL,
          amount INTEGER NOT NULL,month_uncertain INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS clans(tag TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS cwl_hints(season TEXT,tag TEXT REFERENCES accounts(tag),clan_tag TEXT NOT NULL,
          PRIMARY KEY(season,tag));
        CREATE TABLE IF NOT EXISTS rosters(season TEXT,tag TEXT,clan_tag TEXT,clan_name TEXT,
          PRIMARY KEY(season,tag,clan_tag));
        CREATE TABLE IF NOT EXISTS wars(war TEXT,tag TEXT,season TEXT,stars INTEGER,PRIMARY KEY(war,tag));
        '''); self.db.commit()
    def running(self):
        return self.db.execute('SELECT * FROM sessions WHERE ended IS NULL').fetchone()
    def latest(self):
        return self.db.execute('SELECT * FROM sessions ORDER BY id DESC LIMIT 1').fetchone()
    def players(self):
        return list(self.db.execute('SELECT * FROM players WHERE active=1 ORDER BY name,id'))
    def archived_players(self):
        return list(self.db.execute('SELECT * FROM players WHERE active=0 ORDER BY name,id'))
    def restore_player(self,player):
        row=self.db.execute('SELECT * FROM players WHERE id=? AND active=0',(player,)).fetchone()
        if not row: raise ValueError('Игрок не найден в архиве.')
        if self.db.execute('SELECT 1 FROM players WHERE active=1 AND name=?',(row['name'],)).fetchone():
            raise ValueError('Игрок с таким именем уже существует. Сначала измените его имя.')
        # Accounts remain archived until explicitly re-added with a fresh API baseline.
        with self.db: self.db.execute('UPDATE players SET active=1 WHERE id=?',(player,))
    def accounts(self):
        return list(self.db.execute('SELECT a.*,p.name FROM accounts a JOIN players p ON p.id=a.player WHERE a.active=1 ORDER BY p.name,a.tag'))
    def add_player(self,name):
        name=name.strip()
        if not name: raise ValueError('Введите имя игрока.')
        if self.db.execute('SELECT 1 FROM players WHERE active=1 AND name=?',(name,)).fetchone():
            raise ValueError('Игрок с таким именем уже существует.')
        with self.db: return self.db.execute('INSERT INTO players(name) VALUES(?)',(name,)).lastrowid
    def rename(self,player,name):
        name=name.strip()
        if not name: raise ValueError('Имя не может быть пустым.')
        if self.db.execute('SELECT 1 FROM players WHERE active=1 AND name=? AND id<>?',(name,player)).fetchone():
            raise ValueError('Такое имя уже используется.')
        with self.db: self.db.execute('UPDATE players SET name=? WHERE id=?',(name,player))
    def add_account(self,player,profile,time=None):
        t=time or now(); key=tag(profile['tag']); value=friend(profile)
        if not self.db.execute('SELECT 1 FROM players WHERE id=? AND active=1',(player,)).fetchone():
            raise ValueError('Игрок не найден.')
        old=self.db.execute('SELECT * FROM accounts WHERE tag=?',(key,)).fetchone()
        if old:
            if old['active']: raise ValueError('Этот аккаунт уже добавлен.')
            if old['player']!=player:
                raise ValueError('В архиве аккаунт закреплён за другим игроком. Переназначение изменит историю и пока недоступно.')
            if t<=dt.datetime.fromisoformat(old['last_time']):
                raise ValueError('Получен устаревший снимок.')
            if value<old['friend']:
                raise ValueError(key+': достижение уменьшилось. Добавление отменено для проверки данных.')
        clan=profile.get('clan') or {}
        with self.db:
            self.db.execute('INSERT INTO accounts(tag,player,nick,friend,last_time,clan_tag,clan_name) VALUES(?,?,?,?,?,?,?) '
                'ON CONFLICT(tag) DO UPDATE SET active=1,nick=excluded.nick,friend=excluded.friend,last_time=excluded.last_time,clan_tag=excluded.clan_tag,clan_name=excluded.clan_name',
                (key,player,profile['name'],value,t.isoformat(),clan.get('tag'),clan.get('name')))
            s=self.running()
            if s:
                self.db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(session,tag) DO UPDATE SET '
                    'baseline=excluded.baseline-(entries.last_value-entries.baseline),last_value=excluded.last_value,ended=NULL',
                    (s['id'],key,player,value,value,t.isoformat()))
    def _apply(self,profiles,t,keys=None):
        selected=keys if keys is not None else [a['tag'] for a in self.accounts()]
        s=self.running()
        for key in selected:
            p=profiles[key]
            if tag(p['tag'])!=key: raise ValueError('Тег ответа API не совпадает с запросом.')
            value=friend(p)
            a=self.db.execute('SELECT * FROM accounts WHERE tag=?',(key,)).fetchone()
            if t<=dt.datetime.fromisoformat(a['last_time']): raise ValueError('Получен устаревший снимок.')
            if value<a['friend']: raise ValueError(key+': достижение уменьшилось. Обновление отменено для проверки данных.')
            entry=self.db.execute('SELECT * FROM entries WHERE session=? AND tag=? AND ended IS NULL',(s['id'],key)).fetchone() if s else None
            if entry:
                delta=value-entry['last_value']
                if delta<0: raise ValueError('Отрицательный прирост достижения.')
                if delta:
                    self.db.execute('INSERT INTO events(session,tag,player,time,amount) VALUES(?,?,?,?,?)',
                        (s['id'],key,entry['player'],t.isoformat(),delta))
                self.db.execute('UPDATE entries SET last_value=? WHERE session=? AND tag=?',(value,s['id'],key))
            clan=p.get('clan') or {}
            self.db.execute('UPDATE accounts SET nick=?,friend=?,last_time=?,clan_tag=?,clan_name=? WHERE tag=?',
                (p['name'],value,t.isoformat(),clan.get('tag'),clan.get('name'),key))
    def recalculate(self,profiles,time=None):
        with self.db: self._apply(profiles,time or now())
    def start(self,profiles,time=None):
        if self.running(): raise ValueError('Подсчёт уже идёт.')
        if not self.accounts(): raise ValueError('Сначала добавьте аккаунты.')
        t=time or now()
        with self.db:
            self._apply(profiles,t)
            sid=self.db.execute('INSERT INTO sessions(started) VALUES(?)',(t.isoformat(),)).lastrowid
            for a in self.accounts():
                self.db.execute('INSERT INTO entries VALUES(?,?,?,?,?,?,NULL)',(sid,a['tag'],a['player'],a['friend'],a['friend'],t.isoformat()))
        return sid
    def finish(self,profiles,time=None,*,expected_sid=None):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            s=self.running()
            if expected_sid is not None and (not s or s['id']!=expected_sid):
                raise ValueError(f'Период №{expected_sid} уже завершён. Проверьте текущий период перед новым запросом.')
            if not s: raise ValueError('Нет активного подсчёта.')
            t=time or now()
            self._apply(profiles,t)
            self.db.execute('UPDATE entries SET ended=? WHERE session=? AND ended IS NULL',(t.isoformat(),s['id']))
            self.db.execute('UPDATE sessions SET ended=? WHERE id=?',(t.isoformat(),s['id']))
        return s['id']
    def archive(self,profiles,keys,player=None,time=None):
        t=time or now()
        with self.db:
            if keys and self.running(): self._apply(profiles,t,keys)
            for key in keys:
                self.db.execute('UPDATE accounts SET active=0 WHERE tag=?',(key,))
                s=self.running()
                if s: self.db.execute('UPDATE entries SET ended=? WHERE session=? AND tag=?',(t.isoformat(),s['id'],key))
            if player is not None: self.db.execute('UPDATE players SET active=0 WHERE id=?',(player,))
    def get_cwl_clan(self,key):
        month=now().astimezone(MSK).strftime('%Y-%m')
        row=self.db.execute('SELECT clan_tag FROM cwl_hints WHERE season=? AND tag=?',(month,tag(key))).fetchone()
        return row[0] if row else None
    def set_cwl_clan(self,key,clan_tag):
        key=tag(key)
        if not self.db.execute('SELECT 1 FROM accounts WHERE tag=? AND active=1',(key,)).fetchone():
            raise ValueError('Аккаунт не найден в активном списке.')
        month=now().astimezone(MSK).strftime('%Y-%m')
        clan_tag=tag(clan_tag) if clan_tag else None
        with self.db:
            if clan_tag:
                self.db.execute('INSERT OR REPLACE INTO cwl_hints VALUES(?,?,?)',(month,key,clan_tag))
            else:
                self.db.execute('DELETE FROM cwl_hints WHERE season=? AND tag=?',(month,key))
    def save_group(self,group):
        s=group['season']
        with self.db:
            for clan in group.get('clans',[]):
                for m in clan.get('members',[]):
                    self.db.execute('INSERT OR REPLACE INTO rosters VALUES(?,?,?,?)',(s,m['tag'],clan['tag'],clan['name']))
        return s
    def save_war(self,key,s,war):
        if war.get('state') not in ('inWar','warEnded'): return
        with self.db:
            for side in ('clan','opponent'):
                c=war.get(side,{})
                for m in c.get('members',[]):
                    self.db.execute('INSERT OR REPLACE INTO rosters VALUES(?,?,?,?)',(s,m['tag'],c['tag'],c['name']))
                    self.db.execute('INSERT OR REPLACE INTO wars VALUES(?,?,?,?)',
                        (key,m['tag'],s,sum(int(a['stars']) for a in m.get('attacks',[]))))
    def rows(self,sid=None):
        s=self.latest() if sid is None else self.db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
        month=now().astimezone(MSK).strftime('%Y-%m')
        if s:
            data=self.db.execute('SELECT a.*,p.name,e.baseline,e.last_value,e.started entry_started,e.ended entry_ended FROM entries e JOIN accounts a ON a.tag=e.tag JOIN players p ON p.id=e.player WHERE e.session=? ORDER BY p.name,a.tag',(s['id'],))
        else:
            data=self.db.execute('SELECT a.*,p.name,NULL baseline,NULL last_value,NULL entry_started,NULL entry_ended FROM accounts a JOIN players p ON p.id=a.player WHERE a.active=1 ORDER BY p.name,a.tag')
        result=[]
        for a in data:
            c=list(self.db.execute('SELECT clan_name FROM rosters WHERE substr(season,1,7)=? AND tag=?',(month,a['tag'])))
            stars=self.db.execute('SELECT SUM(stars) FROM wars WHERE substr(season,1,7)=? AND tag=?',(month,a['tag'])).fetchone()[0]
            result.append(dict(a)|dict(total=(a['last_value']-a['baseline']) if a['baseline'] is not None else 0,
                cwl_clan=', '.join(dict.fromkeys(r[0] for r in c)) or 'Нет данных',stars=stars))
        return result
    def summary(self, sid=None):
        if sid is None:
            latest = self.latest()
            sid = latest['id'] if latest else None
        result = []
        for player in self.db.execute('SELECT * FROM players ORDER BY name,id'):
            periods = list(self.db.execute('''
                SELECT e.session AS period, s.ended, SUM(e.last_value-e.baseline) AS amount
                FROM entries e JOIN sessions s ON s.id=e.session
                WHERE e.player=? GROUP BY e.session,s.ended ORDER BY e.session
            ''', (player['id'],)))
            best = max((p for p in periods if p['ended']),
                       key=lambda p: p['amount'], default=None)
            current = next((p['amount'] for p in periods if p['period'] == sid), 0)
            result.append(dict(player, total=sum(p['amount'] for p in periods), current=current,
                               best_period=best['period'] if best else None,
                               best=best['amount'] if best else 0, periods=periods))
        return result
    def backup(self,path):
        path=Path(path)
        if path.resolve()==self.path.resolve() or (path.exists() and path.samefile(self.path)):
            raise ValueError('Резервную копию нельзя записать поверх рабочей базы. Выберите другой файл.')
        target=sqlite3.connect(path)
        try:
            with target: self.db.backup(target)
        finally: target.close()

class API:
    BASES={'royaleapi':'https://cocproxy.royaleapi.dev/v1','direct':'https://api.clashofclans.com/v1'}
    def __init__(self,token,route='royaleapi'):
        self.token=token.strip()
        if route not in self.BASES: raise ValueError('Неизвестный способ подключения к API.')
        self.route=route; self.base_url=self.BASES[route]
        if not self.token: raise ValueError('В настройках нужен ключ разработчика Supercell API.')
    def get(self,path):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Неверный путь API.')
        request=urllib.request.Request(self.base_url+path,
            headers={'Authorization':'Bearer '+self.token,'Accept':'application/json','User-Agent':'LocalClashTracker/0.2'})
        try:
            with urllib.request.urlopen(request,timeout=20) as response: return json.load(response)
        except urllib.error.HTTPError as exc:
            allowed_ip='IP прокси 45.79.218.79' if self.route=='royaleapi' else 'внешний IP компьютера'
            messages={401:'Ключ API не принят: проверьте его целиком.',
                403:'Доступ запрещён: проверьте ключ разработчика. В ключе должен быть разрешён '+allowed_ip+'.',
                404:'Нет данных для этого тега или группы ЛВК.',429:'Лимит API. Повторите позже.',503:'Технические работы Supercell.'}
            raise RuntimeError(messages.get(exc.code,'Ошибка API '+str(exc.code))) from None
        except urllib.error.URLError: raise RuntimeError('Не удалось подключиться к API. Проверьте интернет.') from None
        except (socket.timeout,TimeoutError): raise RuntimeError('API не ответил за 20 секунд. Повторите запрос.') from None
        except (ValueError,UnicodeError): raise RuntimeError('API вернул некорректный ответ. Повторите запрос.') from None
    def player(self,key): return self.get('/players/'+quote(key,safe=''))
    def players(self,keys):
        result={}; errors=[]
        with ThreadPoolExecutor(max_workers=6) as pool:
            tasks={pool.submit(self.player,k):k for k in keys}
            for task in as_completed(tasks):
                key=tasks[task]
                try:
                    p=task.result(); friend(p)
                    if tag(p['tag'])!=key: raise ValueError('API вернул другой тег.')
                    result[key]=p
                except Exception as exc: errors.append(key+': '+str(exc))
        if errors: raise RuntimeError('Обновление отменено. Предыдущие данные сохранены.\n'+'\n'.join(errors))
        return result

def sync_cwl(store,api):
    errors=[]
    keys={c[0] for c in store.db.execute('SELECT tag FROM clans')}
    keys.update(c[0] for c in store.db.execute('SELECT DISTINCT clan_tag FROM accounts WHERE active=1 AND clan_tag IS NOT NULL'))
    # Keep refreshing the roster clan after a player has left it in-game.
    month=now().astimezone(MSK).strftime('%Y-%m')
    keys.update(row[0] for row in store.db.execute(
        'SELECT DISTINCT r.clan_tag FROM rosters r JOIN accounts a ON a.tag=r.tag '
        'WHERE a.active=1 AND substr(r.season,1,7)=?',(month,)))
    keys.update(row[0] for row in store.db.execute(
        'SELECT DISTINCT h.clan_tag FROM cwl_hints h JOIN accounts a ON a.tag=h.tag '
        'WHERE a.active=1 AND h.season=?',(month,)))
    groups=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks={pool.submit(api.get,'/clans/'+quote(k,safe='')+'/currentwar/leaguegroup'):k for k in keys}
        for task in as_completed(tasks):
            try: groups.append(task.result())
            except Exception as exc: errors.append(tasks[task]+': '+str(exc))
    wars={}
    for group in groups:
        try:
            s=store.save_group(group)
            for rnd in group.get('rounds',[]):
                for key in rnd.get('warTags',[]):
                    if key!='#0': wars[key]=s
        except Exception as exc:
            errors.append('Некорректные данные группы ЛВК: '+str(exc))
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks={pool.submit(api.get,'/clanwarleagues/wars/'+quote(k,safe='')):k for k in wars}
        for task in as_completed(tasks):
            key=tasks[task]
            try: store.save_war(key,wars[key],task.result())
            except Exception as exc: errors.append(key+': '+str(exc))
    return errors
