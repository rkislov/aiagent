"""Private, immutable Office attachments. No caller-supplied filesystem paths."""
import hashlib
import io
import json
import re
import sqlite3
import time
import zipfile
from pathlib import Path
from uuid import uuid4

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

MAX_FILE = 20_000_000
MIME = {'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation'}


def check_package(data, extension):
    if extension not in MIME:
        raise ValueError('Поддерживаются только DOCX, XLSX и PPTX')
    if not 0 < len(data) <= MAX_FILE:
        raise ValueError(f'Нужен непустой файл размером до {MAX_FILE // 1_000_000} МБ')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries=archive.infolist()
            if len(entries)>2000 or sum(x.file_size for x in entries)>50_000_000:
                raise ValueError('Слишком большой распакованный документ')
            names=[x.filename for x in entries]
            required={'docx':'word/document.xml','xlsx':'xl/workbook.xml','pptx':'ppt/presentation.xml'}[extension]
            if required not in names or '[Content_Types].xml' not in names or len(set(names))!=len(names):
                raise ValueError('Формат содержимого не соответствует расширению')
            for entry in entries:
                name=entry.filename.lower()
                if entry.flag_bits & 1 or any(x in name for x in ('vbaproject','/embeddings/','/externallinks/')):
                    raise ValueError('Зашифрованные файлы, макросы и встроенные объекты не поддерживаются')
                if entry.file_size > MAX_FILE:
                    raise ValueError('В документе слишком большой вложенный файл')
                if name.endswith(('.xml','.rels')):
                    if entry.file_size > 10_000_000:
                        raise ValueError('В документе слишком большой раздел XML. Разделите документ на несколько файлов.')
                    node=ElementTree.fromstring(archive.read(entry))
                    if name.endswith('.rels'):
                        for rel in node:
                            if rel.get('TargetMode')=='External' and not (rel.get('Type','').endswith('/hyperlink') and rel.get('Target','').startswith(('https://','http://','mailto:'))):
                                raise ValueError('Документ содержит внешние подключения')
                    if name=='[content_types].xml' and any('macroenabled' in x.get('ContentType','').lower() for x in node):
                        raise ValueError('Документы с макросами не поддерживаются')
    except (zipfile.BadZipFile,ElementTree.ParseError,DefusedXmlException,KeyError) as exc:
        raise ValueError('Повреждённый Office-документ') from exc


class OfficeFiles:
    def __init__(self,root):
        root=Path(root).resolve()
        self.directory=root/'data/office'
        if self.directory.is_symlink() or not self.directory.resolve().is_relative_to(root/'data'):
            raise ValueError('Недопустимое хранилище документов')
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=self.directory/'files.db'
        if self.db.is_symlink():raise ValueError('Недопустимая база документов')
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,name TEXT,extension TEXT,size INTEGER,sha256 TEXT,created REAL,source TEXT)')
        self.db.chmod(0o600)

    def connect(self):
        db=sqlite3.connect(self.db,timeout=15)
        db.row_factory=sqlite3.Row
        return db

    def info(self,file_id):
        if not isinstance(file_id,str) or not re.fullmatch('[a-f0-9]{32}',file_id):raise ValueError('Недопустимый ID файла')
        with self.connect() as db:row=db.execute('SELECT * FROM files WHERE id=?',(file_id,)).fetchone()
        if row is None:raise ValueError('Файл не найден в вашем пространстве')
        return {**dict(row),'url':'/api/files/'+row['id']+'/download'}

    def path(self,file_id):
        row=self.info(file_id)
        path=self.directory/(row['id']+'.'+row['extension'])
        if path.is_symlink() or not path.is_file():raise ValueError('Файл недоступен')
        return path

    def read(self,file_id):
        path=self.path(file_id)
        if path.stat().st_size>MAX_FILE:raise ValueError('Файл слишком большой')
        data=path.read_bytes()
        row=self.info(file_id)
        if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Содержимое файла изменилось')
        check_package(data,row['extension'])
        return row,data

    def list(self):
        with self.connect() as db:rows=db.execute('SELECT id FROM files ORDER BY created DESC LIMIT 100').fetchall()
        return [self.info(row['id']) for row in rows]

    def save(self,name,data,source=''):
        if not isinstance(name,str) or len(name)>180 or any(c in name for c in '/\\\r\n\x00') or not name.strip():
            raise ValueError('Укажите имя файла без каталогов (до 180 символов)')
        extension=Path(name).suffix.lstrip('.').lower()
        check_package(data,extension)
        file_id=uuid4().hex
        path=self.directory/(file_id+'.'+extension)
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                count,size=db.execute('SELECT count(*),coalesce(sum(size),0) FROM files').fetchone()
                if count>=500 or size+len(data)>250_000_000:raise ValueError('Хранилище документов заполнено (500 файлов / 250 МБ)')
                with path.open('xb') as handle:
                    path.chmod(0o600)
                    handle.write(data)
                db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?)',(file_id,name,extension,len(data),hashlib.sha256(data).hexdigest(),time.time(),source))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return self.info(file_id)
