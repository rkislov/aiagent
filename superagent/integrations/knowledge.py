"""Small per-user SQLite vector store with exact cosine search; no daemon."""
import hashlib
import heapq
import json
import math
import sqlite3
import time
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
    provider=config.providers.get(settings.provider)
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
        with self.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS chunks(file TEXT,part INTEGER,name TEXT,text TEXT,vector TEXT,signature TEXT,created REAL,PRIMARY KEY(file,part))')
        self.path.chmod(0o600)

    def connect(self):return sqlite3.connect(self.path,timeout=15)

    def list(self):
        with self.connect() as db:rows=db.execute('SELECT file,name,count(*),max(created) FROM chunks GROUP BY file,name ORDER BY max(created) DESC').fetchall()
        return {'documents':[{'file_id':f,'name':name,'chunks':n,'indexed_at':at} for f,name,n,at in rows],'storage':'SQLite, exact cosine similarity','max_chunks':5000}

    def index(self,config,file_id):
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
            count=db.execute('SELECT count(*) FROM chunks WHERE file!=?',(file_id,)).fetchone()[0]
            if count+len(chunks)>5000:raise ValueError('База знаний заполнена: максимум 5000 фрагментов')
            db.execute('DELETE FROM chunks WHERE file=?',(file_id,))
            db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',[(file_id,i,row['name'],part,json.dumps(v),key,time.time()) for i,(part,v) in enumerate(zip(chunks,vectors))])
        return {'indexed':True,'file_id':file_id,'chunks':len(chunks),'model':config.embeddings.model}

    def search(self,config,query,limit):
        if not query.strip() or len(query)>4000 or not 1<=limit<=10:raise ValueError('Запрос: 1–4000 символов; limit: 1–10')
        key=signature(config)
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM chunks WHERE signature=? LIMIT 1',(key,)).fetchone():return {'matches':[],'notice':'Нет документов для текущей embedding-модели. Добавьте файлы или переиндексируйте их.'}
        vector=embed(config,[query])[0]
        def scored():
            with self.connect() as db:
                for file,part,name,text,raw in db.execute('SELECT file,part,name,text,vector FROM chunks WHERE signature=?',(key,)):
                    value=json.loads(raw)
                    if len(value)!=len(vector):raise ValueError('Размерность модели изменилась. Переиндексируйте документы.')
                    yield (sum(a*b for a,b in zip(vector,value)),file,part,name,text)
        best=heapq.nlargest(limit,scored())
        return {'matches':[{'score':round(score,6),'file_id':file,'part':part,'name':name,'text':text,'url':'/api/files/'+file+'/download'} for score,file,part,name,text in best]}

    def forget(self,file_id):
        OfficeFiles(self.root).info(file_id)
        with self.connect() as db:count=db.execute('DELETE FROM chunks WHERE file=?',(file_id,)).rowcount
        return {'removed_chunks':count,'file_preserved':True}


def run_knowledge(root,config,name,payload):
    store=Knowledge(root)
    if name=='knowledge_list':return store.list()
    if name=='knowledge_index':return store.index(config,payload['file_id'])
    if name=='knowledge_search':return store.search(config,payload['query'],payload['limit'])
    return store.forget(payload['file_id'])
