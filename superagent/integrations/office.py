"""Bounded Office operations; edits always create a new private version."""
import io
import json
import math
import re

from .office_files import OfficeFiles

OFFICE_TOOLS={'office_list','office_read','office_create','office_edit','office_compare'}


def bounded(value):
    if len(json.dumps(value,ensure_ascii=False,allow_nan=False))>200_000:
        raise ValueError('Содержимое операции превышает 200 КБ')


def fields(value,allowed):
    if not isinstance(value,dict) or set(value)-set(allowed):raise ValueError('Неизвестные поля: '+', '.join(allowed))


def text(value):
    if not isinstance(value,str) or len(value)>20000 or any(ord(c)<32 and c not in '\n\t\r' for c in value):raise ValueError('Некорректный текст (до 20000 символов)')
    return value


def cell(value):
    if value is None or isinstance(value,(bool,int)):return value
    if isinstance(value,float) and math.isfinite(value):return value
    value=text(value)
    if value.startswith('='):
        # No DDE, external workbooks, network functions or defined names.
        formula=value.upper()
        names=re.findall(r'([A-Z_][A-Z_0-9.]*)\s*\(',formula)
        if any(n not in {'SUM','AVERAGE','MIN','MAX','COUNT','COUNTA','IF','ROUND','ABS','AND','OR','SUMIF','COUNTIF'} for n in names) or re.search(r'[\[\]!:|\\"\x00]',formula.replace(':','')):
            raise ValueError('Разрешены только локальные формулы SUM/AVERAGE/MIN/MAX/COUNT/IF/ROUND и арифметика')
    return value


def rows(value):
    if not isinstance(value,list) or len(value)>2000:raise ValueError('Таблица: не более 2000 строк')
    if any(not isinstance(row,list) or len(row)>100 for row in value) or sum(map(len,value))>20000:raise ValueError('Таблица: до 100 колонок и 20000 ячеек')
    return [[cell(v) for v in row] for row in value]


def add_blocks(document,blocks):
    if not isinstance(blocks,list) or len(blocks)>500:raise ValueError('Не более 500 блоков Word')
    for block in blocks:
        fields(block,{'type','text','level','rows'})
        kind=block.get('type')
        if kind=='paragraph':document.add_paragraph(text(block.get('text','')))
        elif kind=='heading':
            level=block.get('level',1)
            if type(level) is not int or not 1<=level<=3:raise ValueError('Уровень заголовка: 1–3')
            document.add_heading(text(block.get('text','')),level)
        elif kind=='table':
            data=rows(block.get('rows',[]))
            if not data or not max(map(len,data)):raise ValueError('Таблица пуста')
            table=document.add_table(rows=len(data),cols=max(map(len,data)))
            table.style='Table Grid'
            for i,row in enumerate(data):
                for j,value in enumerate(row):table.cell(i,j).text='' if value is None else str(value)
        else:raise ValueError('Блок Word: paragraph, heading или table')


def add_slide(presentation,spec):
    from pptx.util import Inches,Pt
    from pptx.dml.color import RGBColor
    fields(spec,{'title','text','rows','notes'})
    slide=presentation.slides.add_slide(presentation.slide_layouts[5])
    title=text(spec.get('title',''))
    if len(title)>160:raise ValueError('Заголовок слайда: до 160 символов')
    slide.shapes.title.text=title
    slide.shapes.title.text_frame.paragraphs[0].font.size=Pt(30)
    slide.shapes.title.text_frame.paragraphs[0].font.color.rgb=RGBColor.from_string('1B365D')
    lines=spec.get('text',[])
    if not isinstance(lines,list) or len(lines)>(4 if spec.get('rows') else 12):raise ValueError('До 12 пунктов на слайде (до 4 при наличии таблицы); подробности перенесите в notes')
    if sum(len(text(line)) for line in lines)>(360 if spec.get('rows') else 1200):raise ValueError('Слишком много текста для одного слайда; разделите его на несколько')
    if lines:
        frame=slide.shapes.add_textbox(Inches(.6),Inches(1.4),Inches(12),Inches(2.2 if spec.get('rows') else 5)).text_frame
        frame.word_wrap=True
        from pptx.enum.text import MSO_AUTO_SIZE
        frame.auto_size=MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        for i,line in enumerate(lines):
            p=frame.paragraphs[0] if not i else frame.add_paragraph()
            p.text=text(line);p.font.size=Pt(20)
    if spec.get('rows'):
        data=rows(spec['rows'])
        width=max(map(len,data))
        if len(data)>8 or width>8:raise ValueError('Таблица на слайде: до 8 строк и 8 колонок')
        table=slide.shapes.add_table(len(data),width,Inches(.6),Inches(3.8 if lines else 1.5),Inches(12),Inches(2.5)).table
        for i,row in enumerate(data):
            for j,value in enumerate(row):
                target=table.cell(i,j);target.text='' if value is None else str(value)
                for paragraph in target.text_frame.paragraphs:paragraph.font.size=Pt(11)
    if spec.get('notes'):slide.notes_slide.notes_text_frame.text=text(spec['notes'])


def create_bytes(extension,content):
    bounded(content)
    if extension=='docx':
        from docx import Document
        from docx.shared import Pt
        fields(content,{'title','blocks'})
        document=Document()
        document.styles['Normal'].font.name='Arial';document.styles['Normal'].font.size=Pt(11)
        if content.get('title'):document.add_heading(text(content['title']),0)
        add_blocks(document,content.get('blocks',[]))
    elif extension=='xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font,PatternFill
        fields(content,{'sheets'})
        sheets=content.get('sheets',[])
        if not isinstance(sheets,list) or not 1<=len(sheets)<=20:raise ValueError('Нужно от 1 до 20 листов Excel')
        document=Workbook();document.remove(document.active)
        titles=set()
        for spec in sheets:
            fields(spec,{'name','rows'})
            name=text(spec.get('name',''))
            if not name or len(name)>31 or re.search(r'[\\/*?:\[\]]',name) or name.casefold() in titles:raise ValueError('Некорректное или повторяющееся имя листа')
            titles.add(name.casefold())
            sheet=document.create_sheet(name)
            for row in rows(spec.get('rows',[])):sheet.append(row)
            sheet.freeze_panes='A2'
            for c in sheet[1]:c.font=Font(bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='1B365D')
            for column in sheet.columns:sheet.column_dimensions[column[0].column_letter].width=min(48,max(14,max(len(str(c.value or '')) for c in column)+2))
    elif extension=='pptx':
        from pptx import Presentation
        from pptx.util import Inches
        fields(content,{'slides'})
        slides=content.get('slides',[])
        if not isinstance(slides,list) or not 1<=len(slides)<=50:raise ValueError('Нужно от 1 до 50 слайдов')
        document=Presentation();document.slide_width=Inches(13.333);document.slide_height=Inches(7.5)
        for spec in slides:add_slide(document,spec)
    else:raise ValueError('Формат: docx, xlsx или pptx')
    output=io.BytesIO();document.save(output)
    return output.getvalue()


def paragraphs(document):
    yield from document.paragraphs
    for table in document.tables:
        for row in table.rows:
            for c in row.cells:yield from c.paragraphs
    for section in document.sections:
        yield from section.header.paragraphs
        yield from section.footer.paragraphs


def replace_text(paragraph,old,new):
    # Match across runs without destroying the formatting of surrounding runs.
    positions=[m.start() for m in re.finditer(re.escape(old),paragraph.text)]
    for start in reversed(positions):
        end=start+len(old);offset=0;first=True
        for run in paragraph.runs:
            value=run.text;left=offset;right=offset+len(value);offset=right
            if right<=start or left>=end:continue
            prefix=value[:max(0,start-left)]
            suffix=value[max(0,end-left):] if end<right else ''
            run.text=prefix+(new if first else '')+suffix;first=False
        if first:raise ValueError('Не удалось изменить сложный текстовый элемент')
    return len(positions)


def edit_bytes(extension,data,changes):
    bounded(changes)
    stream = io.BytesIO(data) if isinstance(data, (bytes, bytearray, memoryview)) else data
    if extension=='docx':
        from docx import Document
        fields(changes,{'replace','append_blocks'})
        document=Document(stream)
        replacements=changes.get('replace',[])
        if not isinstance(replacements,list) or len(replacements)>100:raise ValueError('До 100 замен')
        for entry in replacements:
            fields(entry,{'old','new'})
            old=text(entry.get('old',''));new=text(entry.get('new',''))
            if not old or old==new:raise ValueError('Для замены нужны разные непустые old и new')
            found=sum(replace_text(p,old,new) for p in paragraphs(document))
            if not found:raise ValueError('Текст для замены не найден: '+old[:80])
        add_blocks(document,changes.get('append_blocks',[]))
    elif extension=='xlsx':
        from openpyxl import load_workbook
        fields(changes,{'cells'})
        document=load_workbook(stream,keep_links=False)
        cells=changes.get('cells',[])
        if not isinstance(cells,list) or len(cells)>2000:raise ValueError('До 2000 изменений ячеек')
        for entry in cells:
            fields(entry,{'sheet','cell','value'})
            address=entry.get('cell','')
            if not re.fullmatch('[A-Z]{1,3}[1-9][0-9]{0,5}',address) or entry.get('sheet') not in document.sheetnames:raise ValueError('Укажите существующий лист и адрес ячейки')
            target=document[entry['sheet']][address]
            if target.column>100 or target.row>20000:raise ValueError('Изменения ограничены 100 колонками и 20000 строками')
            target.value=cell(entry.get('value'))
    elif extension=='pptx':
        from pptx import Presentation
        fields(changes,{'replace','append_slides'})
        document=Presentation(stream)
        replacements=changes.get('replace',[])
        if not isinstance(replacements,list) or len(replacements)>100:raise ValueError('До 100 замен')
        for entry in replacements:
            fields(entry,{'old','new'})
            old=text(entry.get('old',''));new=text(entry.get('new',''))
            if not old or old==new:raise ValueError('Для замены нужны разные old и new')
            found=0
            for slide in document.slides:
                for shape in slide.shapes:
                    frames=([shape.text_frame] if shape.has_text_frame else [])+([c.text_frame for row in shape.table.rows for c in row.cells] if shape.has_table else [])
                    for frame in frames:
                        for p in frame.paragraphs:found+=replace_text(p,old,new)
            if not found:raise ValueError('Текст для замены не найден')
        slides=changes.get('append_slides',[])
        if not isinstance(slides,list) or len(slides)+len(document.slides)>50:raise ValueError('До 50 слайдов')
        for spec in slides:add_slide(document,spec)
    else:raise ValueError('Неподдерживаемый формат')
    output=io.BytesIO();document.save(output)
    return output.getvalue()


def extract(extension,data):
    stream = io.BytesIO(data) if isinstance(data, (bytes, bytearray, memoryview)) else data
    entries=[];size=0
    def add(path,value):
        nonlocal size
        value=value.isoformat() if hasattr(value,'isoformat') else value
        size+=len(str(value))
        if len(entries)>=20000 or size>500000:raise ValueError('Документ слишком велик для анализа (20000 элементов / 500000 символов)')
        entries.append({'path':path,'value':value})
    if extension=='docx':
        from docx import Document
        document=Document(stream)
        for i,p in enumerate(document.paragraphs):add(f'paragraph/{i+1}',p.text)
        for t,table in enumerate(document.tables):
            for i,row in enumerate(table.rows):
                for j,c in enumerate(row.cells):add(f'table/{t+1}/{i+1}/{j+1}',c.text)
        for i,section in enumerate(document.sections):
            for label,container in [('header',section.header),('footer',section.footer)]:
                for j,p in enumerate(container.paragraphs):add(f'{label}/{i+1}/{j+1}',p.text)
    elif extension=='xlsx':
        from openpyxl import load_workbook
        document=load_workbook(stream,read_only=True,data_only=False,keep_links=False)
        try:
            for sheet in document:
                add('sheet/'+sheet.title,sheet.title)
                if sheet.max_row>20000 or sheet.max_column>100:raise ValueError('Лист превышает предел анализа: 20000 строк / 100 колонок')
                for row in sheet.iter_rows():
                    for c in row:
                        if c.value is not None:add(sheet.title+'/'+c.coordinate,c.value)
        finally:document.close()
    else:
        from pptx import Presentation
        document=Presentation(stream)
        if len(document.slides)>100:raise ValueError('Презентация содержит более 100 слайдов')
        for i,slide in enumerate(document.slides):
            add(f'slide/{i+1}', '')
            for j,shape in enumerate(slide.shapes):
                if shape.has_text_frame:add(f'slide/{i+1}/text/{j+1}',shape.text)
                if shape.has_table:
                    for r,row in enumerate(shape.table.rows):
                        for c,value in enumerate(row.cells):add(f'slide/{i+1}/table/{j+1}/{r+1}/{c+1}',value.text)
            if slide.has_notes_slide:add(f'slide/{i+1}/notes',slide.notes_slide.notes_text_frame.text)
    return entries


def run_office(root,name,payload):
    store=OfficeFiles(root)
    if name=='office_list':return {'files':store.list()}
    if name=='office_create':
        extension=payload['format']
        return {'file':store.save(payload['name'],create_bytes(extension,payload['content']))} if payload['name'].lower().endswith('.'+extension) else _bad_extension()
    with store.open(payload['file_id']) as (row, stream):
        if name=='office_edit':
            if not payload['name'].lower().endswith('.'+row['extension']):return _bad_extension()
            return {'file':store.save(payload['name'],edit_bytes(row['extension'],stream,payload['changes']),row['id'])}
        entries=extract(row['extension'],stream)
    if name=='office_read':
        start,limit=payload['offset'],payload['limit']
        if not 0<=start<=20000 or not 1<=limit<=500:raise ValueError('offset: 0–20000; limit: 1–500')
        return {'file':row,'entries':entries[start:start+limit],'total':len(entries),'next_offset':start+limit if start+limit<len(entries) else None,'scope':'Текст, таблицы и формулы; без OCR, расчёта формул и анализа оформления.'}
    left={e['path']:e['value'] for e in entries}
    with store.open(payload['other_id']) as (other, stream):
        if row['extension']!=other['extension']:raise ValueError('Сравнивайте документы одного формата')
        right={e['path']:e['value'] for e in extract(other['extension'],stream)}
    changed=[{'path':key,'before':left.get(key),'after':right.get(key),'kind':'added' if key not in left else 'removed' if key not in right else 'changed'} for key in sorted(left.keys()|right.keys()) if key not in left or key not in right or left[key]!=right[key]]
    return {'equal':not changed,'total_changes':len(changed),'changes':changed[:500],'truncated':len(changed)>500,'scope':'Сравнение текста, таблиц и формул по позиции; оформление, изображения и вычисленные значения формул не сравниваются.'}


def _bad_extension():
    raise ValueError('Расширение имени должно соответствовать формату документа')
