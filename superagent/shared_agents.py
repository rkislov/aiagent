"""Shared definitions only. Credentials, code and memory never leave a tenant."""
import json
import sqlite3
from uuid import uuid4
from .gateway.models import Agent
from .integrations.catalog import BUILTINS


class SharedAgents:
    def __init__(self,root):
        self.path=root/'data/shared-agents.db'
        with self.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY,owner TEXT,name TEXT,definition TEXT,UNIQUE(owner,name))')
        self.path.chmod(0o600)

    def connect(self):return sqlite3.connect(self.path,timeout=15)

    def list(self):
        with self.connect() as db:rows=db.execute('SELECT id,owner,name,definition FROM agents ORDER BY name,id LIMIT 200').fetchall()
        return [{'id':i,'owner':owner,'original_name':name,'agent':self.agent(i,value).model_dump()} for i,owner,name,value in rows]

    def agent(self,id_,value):
        agent=Agent.model_validate_json(value)
        agent.name=id_
        agent.memory.path='data/shared/'+id_+'.db'
        return agent

    def get(self,id_):
        with self.connect() as db:row=db.execute('SELECT definition FROM agents WHERE id=?',(id_,)).fetchone()
        if not row:raise ValueError('Общий агент больше недоступен')
        return self.agent(id_,row[0])

    def publish(self,owner,agent):
        if agent.name.startswith('shared_'):raise ValueError('Создайте личного агента для публикации')
        if set(agent.skills)-BUILTINS.keys():raise ValueError('Общий агент может использовать встроенные навыки. Пользовательский Python-код не публикуется.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT id FROM agents WHERE owner=? AND name=?',(owner,agent.name)).fetchone()
            if not row and db.execute('SELECT count(*) FROM agents').fetchone()[0]>=200:raise ValueError('Каталог общих агентов заполнен')
            id_=row[0] if row else 'shared_'+uuid4().hex[:24]
            db.execute('INSERT INTO agents VALUES(?,?,?,?) ON CONFLICT(owner,name) DO UPDATE SET definition=excluded.definition',(id_,owner,agent.name,agent.model_dump_json()))
        return id_

    def unpublish(self,actor,id_,admin=False):
        with self.connect() as db:
            row=db.execute('SELECT owner FROM agents WHERE id=?',(id_,)).fetchone()
            if not row or (row[0]!=actor and not admin):raise PermissionError('Снять публикацию может автор или администратор')
            db.execute('DELETE FROM agents WHERE id=?',(id_,))
