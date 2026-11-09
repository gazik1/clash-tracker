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
    def __init__(self,name,widths):
        self.name=name; self.widths=widths; self.cells={}; self.merges=[]
        self.auto_filter_ref=None; self.heights={}
        self.hidden_columns=set(); self.filter_buttons=set()
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
        columns=''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"'
                        + (' hidden="1"' if i in self.hidden_columns else '') + '/>'
                        for i,w in enumerate(self.widths,1))
        data=''.join(f'<row r="{r}" ht="{self.heights.get(r,48 if r==2 else 38 if r==4 else 32)}" customHeight="1">'+''.join(cells)+'</row>' for r,cells in sorted(rows.items()))
        auto_filter=''
        if self.auto_filter_ref:
            buttons=''.join(f'<filterColumn colId="{i-1}" hiddenButton="1"/>'
                            for i in range(1,len(self.widths)+1) if i not in self.filter_buttons)
            auto_filter=f'<autoFilter ref="{self.auto_filter_ref}">{buttons}</autoFilter>'
        merges='<mergeCells count="'+str(len(self.merges))+'">'+''.join(f'<mergeCell ref="{m}"/>' for m in self.merges)+'</mergeCells>' if self.merges else ''
        return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{NS}"><sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="22"/><cols>{columns}</cols><sheetData>{data}</sheetData>{auto_filter}{merges}<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/><pageSetup orientation="landscape" paperSize="9"/></worksheet>'

STRIPED, NUMBER, STRIPED_NUMBER, DONATION, STRIPED_DONATION = range(5,10)

def table_style(fill, *, number=False, bold=False):
    number_format=3 if number else 0
    font=3 if bold else 0
    alignment='horizontal="right"' if number else 'wrapText="1"'
    return (f'<xf numFmtId="{number_format}" fontId="{font}" fillId="{fill}" borderId="1" xfId="0" '
            f'applyNumberFormat="1" applyFill="1" applyFont="1" applyAlignment="1">'
            f'<alignment vertical="center" {alignment}/></xf>')

STYLES=f'''<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="{NS}">
<fonts count="4"><font><sz val="11"/><name val="Calibri"/><color rgb="FF203044"/></font><font><b/><sz val="11"/><name val="Calibri"/><color rgb="FFFFFFFF"/></font><font><b/><sz val="18"/><name val="Calibri"/><color rgb="FF163550"/></font><font><b/><sz val="11"/><name val="Calibri"/><color rgb="FF095365"/></font></fonts>
<fills count="6"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF163550"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEAF3F8"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF3F7FA"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFDDECF3"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border/><border><left style="hair"><color rgb="FFCAD8E1"/></left><right style="hair"><color rgb="FFCAD8E1"/></right><top style="hair"><color rgb="FFCAD8E1"/></top><bottom style="hair"><color rgb="FFCAD8E1"/></bottom></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="10"><xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/><xf numFmtId="3" fontId="0" fillId="3" borderId="1" xfId="0" applyAlignment="1"><alignment vertical="center" horizontal="right"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>{table_style(4)}{table_style(0,number=True)}{table_style(4,number=True)}{table_style(3,number=True,bold=True)}{table_style(5,number=True,bold=True)}</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''

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
        period=f'Период №{session["id"]} открыт с {start} МСК.'
        if session['ended']:
            period=f'Период №{session["id"]} завершён. {start} — {date(session["ended"])} МСК.'
        period+=f' Выгружено {timestamp} МСК.'
    a=base('Аккаунты',[25,24,15,19,24,15,21,24,16], 'Аккаунты игроков',period)
    a.heights[2]=34
    a.hidden_columns.add(9)
    a.row(4,['Игрок','Ник','Тег','Донат за период','Клан ЛВК ('+current+')','Звёзды ЛВК',
             'Последний запрос, МСК','Учёт в периоде','ID игрока'],1)
    friend_note=period if session else 'Подсчёт ещё не начат. Показаны последние сохранённые значения.'
    friend_sheet=base('Настоящий друг',[28,29,17,23,27,17,42], 'Пожертвования · Настоящий друг',friend_note)
    friend_sheet.row(4,['Игрок','Ник','Тег','База на старте','Последнее показание','Разница','Правки начала'],1)
    edits=defaultdict(list)
    if session:
        for edit in store.db.execute('''
            SELECT e.tag,b.old_value,b.new_value
            FROM baseline_edits b JOIN events e ON e.id=b.event
            WHERE e.session=? ORDER BY b.event
        ''',(session['id'],)):
            edits[edit['tag']].append(f"{edit['old_value']:,} → {edit['new_value']:,}".replace(',',' '))
    groups=defaultdict(list)
    for row in rows:
        groups[row['player']].append(row)
    rr=5
    for player_id,group in groups.items():
        first=rr
        for r in group:
            striped=rr%2==0
            status='Учитывается' if session else 'Период не начат'
            if r['entry_ended']:
                status='Завершён '+date(r['entry_ended'])
            stars=r['stars'] if r['stars'] is not None else 'Нет данных'
            name=r['name'] if rr==first else ''
            a.row(rr,[name,r['nick'],r['tag'],r['total'],r['cwl_clan'],stars,date(r['last_time']),status,player_id],
                  STRIPED if striped else 0)
            a.put(rr,4,r['total'],STRIPED_DONATION if striped else DONATION)
            friend_sheet.row(rr,[name,r['nick'],r['tag']],STRIPED if striped else 0)
            number_style=STRIPED_NUMBER if striped else NUMBER
            friend_sheet.put(rr,5,r['last_value'] if session else r['friend'],number_style)
            if session:
                friend_sheet.put(rr,4,r['baseline'],number_style)
                friend_sheet.put(rr,6,(f'E{rr}-D{rr}',r['total']),STRIPED_DONATION if striped else DONATION)
            else:
                friend_sheet.put(rr,4,'Не начат',STRIPED if striped else 0)
                friend_sheet.put(rr,6,'Не начат',STRIPED if striped else 0)
            changes=edits.get(r['tag'],[])
            friend_sheet.put(rr,7,'\n'.join(changes),STRIPED if striped else 0)
            if len(changes)>1:
                friend_sheet.heights[rr]=min(409,max(32,15*len(changes)+8))
            rr+=1
        a.merge(first,1,rr-1,1)
        friend_sheet.merge(first,1,rr-1,1)
    last_account=rr-1
    if session and rows:
        friend_sheet.row(rr+1,['База берётся на старте периода или при добавлении аккаунта. После возврата база корректируется, чтобы исключить донат за время отсутствия.'],4)
        friend_sheet.merge(rr+1,1,rr+1,7)
        friend_sheet.row(rr+2,['Последнее показание берётся из последнего сохранённого замера аккаунта в выбранном периоде.'],4)
        friend_sheet.merge(rr+2,1,rr+2,7)
    elif not rows:
        friend_sheet.row(5,['Нет показаний за выбранный период' if session else 'Нет добавленных аккаунтов'],4)
        friend_sheet.merge(5,1,5,7)
    if rows:
        a.auto_filter_ref=f'A4:I{last_account}'
        a.filter_buttons={5,6}
    else:
        a.row(5,['Нет аккаунтов в выбранном периоде'],4)
    a.row(max(rr+2,7),['Донат = прирост «Настоящий друг». Показатель не является суммой всех видов пожертвований.'],4)
    a.merge(max(rr+2,7),1,max(rr+2,7),8)
    a.row(max(rr+3,8),[f'ЛВК {current}: сохранённые сведения текущего игрового сезона, независимо от выбранного периода доната. «Нет данных» — не нулевой результат.'],4)
    a.merge(max(rr+3,8),1,max(rr+3,8),8)
    a.row(max(rr+4,9),['Звёзды — сумма по полученным войнам ЛВК; при пропуске данных итог может быть неполным. Клан определяется по составу ЛВК.'],4)
    a.merge(max(rr+4,9),1,max(rr+4,9),8)
    total=base('За всё время',[27,22,17,21,19], 'Донат за всё время и рекорды',f'Сумма всех периодов на момент выгрузки {timestamp} МСК. Паузы между периодами не учитываются.')
    total.row(4,['Игрок','Донат с начала учёта','Статус игрока','Рекорд по донату','Период рекорда'],1)
    summary=base('Итоги по игрокам',[9,30,20,13,22], 'Рейтинг доната',period)
    summary.heights.update({2:34,3:27,4:32})
    summary.put(3,1,'Общий донат за период',4)
    summary.merge(3,1,3,2)
    grand_total=sum(player['current'] for player in summaries)
    summary.put(3,3,(f'SUM(C5:C{4+len(summaries)})',grand_total) if summaries else 0,DONATION)
    summary.row(4,['Место','Игрок','Донат за период','Аккаунты','Донат за всё время'],1)
    ranking=sorted(summaries,key=lambda p:(p['id'] not in groups,-p['current'],p['name'].casefold(),p['id']))
    previous_donation=None
    place=0
    for i,player in enumerate(ranking,5):
        player_id=player['id']
        count=len(groups.get(player_id,()))
        rank='—'
        if session and count:
            if player['current']!=previous_donation:
                place=i-4
                previous_donation=player['current']
            rank=place
        striped=i%2==0
        current_value=player['current']
        if count:
            formula=f"SUMIF('Аккаунты'!I$5:I${last_account},{player_id},'Аккаунты'!D$5:D${last_account})"
            current_value=(formula,player['current'])
        summary.row(i,[rank,player['name'],current_value,count,player['total']],STRIPED if striped else 0)
        summary.put(i,3,current_value,STRIPED_DONATION if striped else DONATION)
        summary.put(i,5,player['total'],STRIPED_NUMBER if striped else NUMBER)
        summary.heights[i]=40 if len(player['name'])>30 else 25
    if not ranking: summary.put(5,1,'Нет игроков для отображения',4); summary.merge(5,1,5,5)
    periods=base('Периоды',[26,18,22,16,20,24,24], 'CLASH · История подсчётов','Границы периода задаются кнопками «Начать» и «Завершить и сохранить Excel».')
    periods.row(4,['Игрок','Период','Донат','ID игрока','Статус периода','Начало, МСК','Конец, МСК'],1)
    journal=base('Журнал',[26,26,20,18,19,15], 'CLASH · Журнал приростов','Журнал содержит приросты достижения и поправки после ручного изменения стартовых значений.')
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
        total.row(i, [player['name'], total_value, 'Активен' if player['active'] else 'Архив',
                      player['best'], player['best_period'] or '—'])
        total.put(i,2,total_value,NUMBER)
        total.put(i,4,player['best'],NUMBER)
    sheets=[summary,a,friend_sheet,total,periods,journal]
    filter_name=(f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="1" hidden="1">'
                 f"'Аккаунты'!$A$4:$I${last_account}</definedName></definedNames>" if a.auto_filter_ref else '')
    destination=Path(path); destination.parent.mkdir(parents=True,exist_ok=True)
    handle,tmp=tempfile.mkstemp(suffix='.xlsx',dir=destination.parent); os.close(handle)
    try:
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'+''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1,len(sheets)+1))+'</Types>')
            z.writestr('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr('xl/workbook.xml',f'<?xml version="1.0"?><workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="{s.name}" sheetId="{i}" r:id="rId{i}"/>' for i,s in enumerate(sheets,1))+f'</sheets>{filter_name}<calcPr calcId="191029" fullCalcOnLoad="1"/></workbook>')
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
