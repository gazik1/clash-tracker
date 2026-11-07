"""Small runtime OOXML exporter: Excel is not required on the user's computer."""
from collections import defaultdict
from pathlib import Path
import datetime as dt
import os
import tempfile
import zipfile
from xml.sax.saxutils import escape
from core import now, MSK

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
def col(n):
    s=''
    while n: n,r=divmod(n-1,26); s=chr(65+r)+s
    return s

def safe(s):
    # XML 1.0 excludes surrogate code points and U+FFFE/U+FFFF.
    return ''.join(c for c in str(s) if c in '\t\n\r' or
                   0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or
                   0x10000 <= ord(c) <= 0x10FFFF)[:32767]

class Sheet:
    def __init__(self,name,widths): self.name=name; self.widths=widths; self.cells={}; self.merges=[]
    def put(self,row,column,value,style=0): self.cells[row,column]=(value,style)
    def row(self,r,values,style=0):
        for c,v in enumerate(values,1): self.put(r,c,v,style)
    def merge(self,r1,c1,r2,c2):
        if (r1,c1)!=(r2,c2): self.merges.append(f'{col(c1)}{r1}:{col(c2)}{r2}')
    def xml(self):
        rows=defaultdict(list)
        for (r,c),(v,style) in sorted(self.cells.items()):
            ref=f'{col(c)}{r}'
            attrs=f'r="{ref}" s="{style}"'
            if isinstance(v,tuple):
                content=f'<f>{escape(v[0])}</f><v>{v[1]}</v>'
            elif isinstance(v,(int,float)): content=f'<v>{v}</v>'
            else:
                attrs+=' t="inlineStr"'
                content='<is><t xml:space="preserve">'+escape(safe('' if v is None else v))+'</t></is>'
            rows[r].append(f'<c {attrs}>{content}</c>')
        columns=''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i,w in enumerate(self.widths,1))
        data=''.join(f'<row r="{r}" ht="{48 if r==2 else 38 if r==4 else 32}" customHeight="1">'+''.join(cells)+'</row>' for r,cells in sorted(rows.items()))
        merges='<mergeCells count="'+str(len(self.merges))+'">'+''.join(f'<mergeCell ref="{m}"/>' for m in self.merges)+'</mergeCells>' if self.merges else ''
        return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{NS}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="22"/><cols>{columns}</cols><sheetData>{data}</sheetData>{merges}<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/><pageSetup orientation="landscape" paperSize="9"/></worksheet>'

STYLES=f'''<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{NS}">
<fonts count="3"><font><sz val="11"/><name val="Calibri"/><color rgb="FF203044"/></font><font><b/><sz val="11"/><name val="Calibri"/><color rgb="FFFFFFFF"/></font><font><b/><sz val="18"/><name val="Calibri"/><color rgb="FF163550"/></font></fonts>
<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF163550"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEAF3F8"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border/><border><left style="hair"><color rgb="FFCAD8E1"/></left><right style="hair"><color rgb="FFCAD8E1"/></right><top style="hair"><color rgb="FFCAD8E1"/></top><bottom style="hair"><color rgb="FFCAD8E1"/></bottom></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5"><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/><xf numFmtId="3" fontId="0" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" horizontal="right"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''

def export(store,path,sid=None):
    session=store.latest() if sid is None else store.db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
    if sid is not None and session is None:
        raise ValueError('Период не найден. Выберите существующий период.')
    rows=store.rows(sid); summaries=store.summary(sid)
    generated=now().astimezone(MSK)
    current=generated.strftime('%Y-%m')
    timestamp=generated.strftime('%d.%m.%Y %H:%M')
    def date(value):
        return dt.datetime.fromisoformat(value).astimezone(MSK).strftime('%d.%m.%Y %H:%M') if value else 'Идёт'
    sessions={s['id']:dict(s) for s in store.db.execute('SELECT * FROM sessions')} 
    def base(name,widths,title,note):
        s=Sheet(name,widths); s.row(1,[title],2); s.merge(1,1,1,min(5,len(widths)))
        s.row(2,[note],4); s.merge(2,1,2,min(8,len(widths)))
        return s
    period='Подсчёт ещё не начат'
    if session:
        start=dt.datetime.fromisoformat(session['started']).astimezone(MSK).strftime('%d.%m.%Y %H:%M')
        end=dt.datetime.fromisoformat(session['ended']).astimezone(MSK).strftime('%d.%m.%Y %H:%M') if session['ended'] else 'идёт'
        period=f'Период #{session["id"]}: {start} → {end} (МСК). Выгружено {timestamp} МСК.'
    a=base('Аккаунты',[24,36,19,32,15,4,24,22,22,27], 'CLASH · Отчёт по аккаунтам',period)
    a.row(4,['Игрок','Ник / тег аккаунта','Донат за период','Клан ЛВК: '+current,'Звёзды ЛВК'],1)
    a.put(4,7,'Игрок',1); a.put(4,8,'Итог за период',1)
    a.put(4,9,'Последний запрос аккаунта, МСК',1); a.put(4,10,'Учёт аккаунта в периоде',1)
    groups=defaultdict(list)
    for r in rows: groups[r['player']].append(r)
    rr=5
    for j,group in enumerate(groups.values(),5):
        first=rr
        for r in group:
            a.row(rr,[r['name'] if rr==first else '',r['nick']+'\n'+r['tag'],r['total'],r['cwl_clan'],r['stars'] if r['stars'] is not None else 'Нет данных'])
            a.put(rr,3,r['total'],3)
            a.put(rr,9,date(r['last_time']))
            a.put(rr,10,('Завершён '+date(r['entry_ended'])) if r['entry_ended'] else
                  'Учитывается' if session else 'Период не начат')
            rr+=1
        a.merge(first,1,rr-1,1)
        a.put(j,7,group[0]['name']); a.put(j,8,(f'SUM(C{first}:C{rr-1})',sum(r['total'] for r in group)),3)
    if not rows: a.row(5,['Нет аккаунтов в выбранном периоде'],4)
    a.row(max(rr+2,7),['Донат = прирост «Настоящий друг». Показатель не является суммой всех видов пожертвований.'],4)
    a.merge(max(rr+2,7),1,max(rr+2,7),8)
    a.row(max(rr+3,8),[f'ЛВК {current}: сохранённые сведения текущего игрового сезона, независимо от выбранного периода доната. «Нет данных» — не нулевой результат.'],4)
    a.merge(max(rr+3,8),1,max(rr+3,8),8)
    a.row(max(rr+4,9),['Звёзды — сумма по полученным войнам ЛВК; при пропуске данных итог может быть неполным. Клан определяется по составу ЛВК.'],4)
    a.merge(max(rr+4,9),1,max(rr+4,9),8)
    total=base('За всё время',[26,24,24], 'CLASH · Общий донат',f'Сумма всех периодов на момент выгрузки {timestamp} МСК. Паузы между периодами не учитываются.')
    total.row(4,['Игрок','Донат с начала учёта','Статус игрока'],1)
    summary=base('Итоги по игрокам',[27,23,23,25,22], 'CLASH · Итоги по игрокам',period)
    current_label = f'Донат за период #{session["id"]}' if session else 'Донат за период'
    summary.row(4,['Игрок',current_label,'Донат за всё время','Лучший период: донат','Период рекорда'],1)
    periods=base('Периоды',[26,18,22,16,20,24,24], 'CLASH · История подсчётов','Границы периода задаются кнопками «Начать» и «Завершить и сохранить Excel».')
    periods.row(4,['Игрок','Период','Донат','ID игрока','Статус периода','Начало, МСК','Конец, МСК'],1)
    journal=base('Журнал',[26,26,20,18,19,15], 'CLASH · Журнал приростов','Каждая запись показывает прирост достижения в открытом на тот момент периоде.')
    journal.row(4,['Игрок','Тег','Время МСК','Период','Донат','ID игрока'],1)
    names={p['id']:p['name'] for p in summaries}
    events=list(store.db.execute('SELECT * FROM events ORDER BY time,id'))
    for i,e in enumerate(events,5):
        t=dt.datetime.fromisoformat(e['time']).astimezone(MSK)
        journal.row(i,[names[e['player']],e['tag'],t.strftime('%d.%m.%Y %H:%M'),e['session'],e['amount'],e['player']])
        journal.put(i,5,e['amount'],3)
    last = max(5, len(events)+4)
    period_row = 5
    for i, player in enumerate(summaries, 5):
        first = period_row
        for result in player['periods']:
            formula = (f"SUMIFS('Журнал'!E$5:E${last},'Журнал'!F$5:F${last},D{period_row},"
                       f"'Журнал'!D$5:D${last},B{period_row})")
            periods.row(period_row, [player['name'], result['period'], (formula, result['amount']),
                                     player['id'], 'Завершён' if result['ended'] else 'Идёт',
                                     date(sessions[result['period']]['started']),date(result['ended'])])
            period_row += 1
        total_value = (f"SUM('Периоды'!C{first}:C{period_row-1})", player['total']) if first < period_row else 0
        total.row(i, [player['name'], total_value, 'Активен' if player['active'] else 'Архив'])
        summary.row(i, [player['name'], player['current'], player['total'],
                        player['best'], player['best_period'] or '—'])
    sheets=[summary,a,total,periods,journal]
    destination=Path(path); destination.parent.mkdir(parents=True,exist_ok=True)
    handle,tmp=tempfile.mkstemp(suffix='.xlsx',dir=destination.parent); os.close(handle)
    try:
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1))+'</Types>')
            z.writestr('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr('xl/workbook.xml',f'<?xml version="1.0"?><workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="{s.name}" sheetId="{i}" r:id="rId{i}"/>' for i,s in enumerate(sheets,1))+'</sheets><calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1))+f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
            z.writestr('xl/styles.xml',STYLES)
            for i,s in enumerate(sheets,1): z.writestr(f'xl/worksheets/sheet{i}.xml',s.xml())
        # Publish the completed workbook without replacing a report at this name.
        if os.name == 'nt':
            os.rename(tmp,destination)
        else:
            os.link(tmp,destination)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return destination
