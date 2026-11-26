"""Small per-user SQLite vector store with exact cosine search; no daemon."""
import hashlib
import heapq
import json
import math
import sqlite3
import time
from uuid import uuid4
from pathlib import Path

from .model_media import model_post
from .office import extract
from .office_files import OfficeFiles

KNOWLEDGE_TOOLS={'knowledge_list','knowledge_index','knowledge_search','knowledge_forget'}


def normalize(vector):
    if not isinstance(vector,list) or not 1<=len(vector)<=8192 or any(type(x) not in (int,float) or not math.isfinite(x) for x in vector):raise ValueError('Некорректный embedding-вектор')
    norm=math.sqrt(sum(x*x for x in vector))
    if not math.isfinite(norm) or norm==0:raise ValueError('Пустой embedding-вектор')
    return [x/norm for x in vector]


def signature(config):
    settings=config.embeddings
    from superagent.core.providers.pool import resolve_provider
    provider=resolve_provider(config,settings.provider)
    if not provider:raise ValueError('Провайдер embedding-модели не найден')
    return hashlib.sha256(json.dumps([settings.provider,provider.base_url,settings.model,settings.dimensions]).encode()).hexdigest()


def embed(config,texts):
    settings=config.embeddings
    if not settings.enabled:raise ValueError('Включите базу знаний и укажите embedding-модель в настройках')
    payload={'model':settings.model,'input':texts,'encoding_format':'float'}
    if settings.dimensions:payload['dimensions']=settings.dimensions
    result=model_post(config,settings.provider,'/embeddings',payload,limit=12_000_000)
    try:
        rows=sorted(result['data'],key=lambda row:row['index'])
        if [r['index'] for r in rows]!=list(range(len(texts))):raise ValueError()
        vectors=[normalize(r['embedding']) for r in rows]
        if len({len(v) for v in vectors})!=1:raise ValueError()
        return vectors
    except (KeyError,TypeError,ValueError):raise ValueError('API вернул некорректные embeddings') from None


class Knowledge:
    def __init__(self,root):
        self.root=Path(root).resolve();self.path=self.root/'data/knowledge.db'
        if self.path.is_symlink() or not self.path.resolve().is_relative_to(self.root/'data'):raise ValueError('Недопустимая база знаний')
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY,name TEXT,description TEXT,created REAL)')
            db.execute('INSERT OR IGNORE INTO collections VALUES(?,?,?,?)',('default','Мои знания','Основная личная база знаний',time.time()))
            old=db.execute('PRAGMA table_info(chunks)').fetchall()
            legacy=bool(old) and 'collection' not in {r[1] for r in old}
            if legacy:db.execute('ALTER TABLE chunks RENAME TO chunks_legacy')
            db.execute('CREATE TABLE IF NOT EXISTS chunks(collection TEXT,file TEXT,part INTEGER,name TEXT,text TEXT,vector TEXT,signature TEXT,created REAL,PRIMARY KEY(collection,file,part))')
            if legacy:
                db.execute("INSERT INTO chunks SELECT 'default',file,part,name,text,vector,signature,created FROM chunks_legacy")
                db.execute('DROP TABLE chunks_legacy')
            db.execute('CREATE INDEX IF NOT EXISTS collection_signature ON chunks(collection,signature)')
        self.path.chmod(0o600)

    def connect(self):return sqlite3.connect(self.path,timeout=15)

    def collections(self):
        with self.connect() as db:rows=db.execute('SELECT c.id,c.name,c.description,c.created,count(k.part),count(distinct k.file) FROM collections c LEFT JOIN chunks k ON k.collection=c.id GROUP BY c.id ORDER BY c.created').fetchall()
        return [dict(zip(('id','name','description','created','chunks','documents'),row)) for row in rows]

    def validate_collections(self,ids):
        if not isinstance(ids,(list,tuple)) or len(ids)>20 or any(not isinstance(x,str) for x in ids) or len(set(ids))!=len(ids):raise ValueError('Выберите до 20 разных личных баз знаний')
        available={row['id'] for row in self.collections()}
        if set(ids)-available:raise ValueError('База знаний не найдена в вашем пространстве')
        return list(ids)

    def create_collection(self,name,description=''):
        if not isinstance(name,str) or not 1<=len(name.strip())<=100 or not isinstance(description,str) or len(description)>500:raise ValueError('Название базы: 1–100 символов; описание до 500')
        id_=uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM collections').fetchone()[0]>=20:raise ValueError('Можно создать до 20 баз знаний')
            db.execute('INSERT INTO collections VALUES(?,?,?,?)',(id_,name.strip(),description,time.time()))
        return next(row for row in self.collections() if row['id']==id_)

    def delete_collection(self,id_):
        self.validate_collections([id_])
        if id_=='default':raise ValueError('Основную базу нельзя удалить; можно удалить из неё отдельные документы')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM chunks WHERE collection=?',(id_,))
            db.execute('DELETE FROM collections WHERE id=?',(id_,))

    def list(self,collections=None):
        selected=self.validate_collections(collections if collections is not None else [c['id'] for c in self.collections()])
        if not selected:rows=[]
        else:
            with self.connect() as db:rows=db.execute('SELECT collection,file,name,count(*),max(created) FROM chunks WHERE collection IN ('+','.join('?' for _ in selected)+') GROUP BY collection,file,name ORDER BY max(created) DESC',selected).fetchall()
        return {'collections':[c for c in self.collections() if c['id'] in selected],'documents':[{'collection_id':c,'file_id':f,'name':name,'chunks':n,'indexed_at':at} for c,f,name,n,at in rows],'storage':'SQLite, exact cosine similarity','max_chunks':5000}

    def index(self,config,file_id,collection_id='default'):
        self.validate_collections([collection_id])
        row,data=OfficeFiles(self.root).read(file_id)
        entries=extract(row['extension'],data)
        content='\n'.join(str(e['path'])+': '+str(e['value']) for e in entries)
        chunks=[content[i:i+2000] for i in range(0,len(content),1800)]
        if not chunks or len(chunks)>128:raise ValueError('Для индексации нужен документ до 128 фрагментов (~230000 символов)')
        key=signature(config)
        vectors=[]
        for i in range(0,len(chunks),32):vectors.extend(embed(config,chunks[i:i+32]))
        if len({len(v) for v in vectors})!=1:raise ValueError('Размерность embeddings изменилась во время индексации')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM collections WHERE id=?',(collection_id,)).fetchone():raise ValueError('База знаний удалена во время индексации')
            count=db.execute('SELECT count(*) FROM chunks WHERE NOT (file=? AND collection=?)',(file_id,collection_id)).fetchone()[0]
            if count+len(chunks)>5000:raise ValueError('База знаний заполнена: максимум 5000 фрагментов')
            db.execute('DELETE FROM chunks WHERE file=? AND collection=?',(file_id,collection_id))
            db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)',[(collection_id,file_id,i,row['name'],part,json.dumps(v),key,time.time()) for i,(part,v) in enumerate(zip(chunks,vectors))])
        return {'indexed':True,'collection_id':collection_id,'file_id':file_id,'chunks':len(chunks),'model':config.embeddings.model}

    def search(self,config,query,limit,collections=None):
        if not query.strip() or len(query)>4000 or not 1<=limit<=10:raise ValueError('Запрос: 1–4000 символов; limit: 1–10')
        selected=self.validate_collections(collections if collections is not None else [c['id'] for c in self.collections()])
        if not selected:return {'matches':[],'notice':'Базы знаний отключены для этого диалога.'}
        key=signature(config)
        where='signature=? AND collection IN ('+','.join('?' for _ in selected)+')'
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM chunks WHERE '+where+' LIMIT 1',[key,*selected]).fetchone():return {'matches':[],'notice':'Нет документов для текущей embedding-модели. Добавьте файлы или переиндексируйте их.'}
        vector=embed(config,[query])[0]
        def scored():
            with self.connect() as db:
                for collection,file,part,name,text,raw in db.execute('SELECT collection,file,part,name,text,vector FROM chunks WHERE '+where,[key,*selected]):
                    value=json.loads(raw)
                    if len(value)!=len(vector):raise ValueError('Размерность модели изменилась. Переиндексируйте документы.')
                    yield (sum(a*b for a,b in zip(vector,value)),collection,file,part,name,text)
        best=heapq.nlargest(limit,scored())
        return {'matches':[{'score':round(score,6),'collection_id':collection,'file_id':file,'part':part,'name':name,'text':text,'url':'/api/files/'+file+'/download'} for score,collection,file,part,name,text in best]}

    def forget(self,file_id,collection_id='default'):
        self.validate_collections([collection_id])
        OfficeFiles(self.root).info(file_id)
        with self.connect() as db:count=db.execute('DELETE FROM chunks WHERE file=? AND collection=?',(file_id,collection_id)).rowcount
        return {'removed_chunks':count,'file_preserved':True}


def run_knowledge(root,config,name,payload,scope=None):
    store=Knowledge(root)
    if scope is not None:store.validate_collections(scope)
    if name=='knowledge_list':return store.list(scope)
    if name=='knowledge_search':return store.search(config,payload['query'],payload['limit'],scope)
    collection_id=payload['collection_id']
    if scope is not None and collection_id not in scope:raise PermissionError('Эта база знаний не подключена к текущему диалогу')
    if name=='knowledge_index':return store.index(config,payload['file_id'],collection_id)
    return store.forget(payload['file_id'],collection_id)
