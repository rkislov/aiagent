"""Persistent per-workspace schedules. Background runs never approve protected actions."""
import json
import hashlib
import sqlite3
import threading
import time
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import uuid4
from pydantic import Field, field_validator
from typing import Literal
from .gateway.models import StrictModel, NAME


class Schedule(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    agent: str = Field(default='', pattern=r'^$|'+NAME)
    model: str = Field(default='demo:office', pattern=r'^[a-z][a-z0-9_-]*:.+$')
    connections: list[str] | None = Field(default=None, max_length=8)
    message: str = Field(min_length=1, max_length=20000)
    mode: Literal['once','interval','daily','weekly'] = 'daily'
    start_at: datetime | None = None
    interval_minutes: int = Field(default=1440, ge=1, le=525600)
    local_time: str = Field(default='09:00', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    timezone: str = 'Europe/Moscow'
    weekday: int = Field(default=0, ge=0, le=6)
    enabled: bool = True
    web_enabled: bool = False
    allow_reads: bool = False

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try: ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError): raise ValueError('Неизвестный часовой пояс') from None
        return value

    @field_validator('start_at')
    @classmethod
    def aware_start(cls, value):
        if value is not None and value.tzinfo is None: raise ValueError('Дата запуска должна включать часовой пояс')
        return value

    def next_time(self, now, initial=False):
        if self.mode in ('once','interval'):
            if initial and self.start_at is not None:
                if self.start_at.timestamp() <= now: raise ValueError('Первый запуск должен быть в будущем')
                return self.start_at.timestamp()
            if self.mode == 'once':
                if initial: raise ValueError('Укажите дату и время запуска')
                return None
            return now + self.interval_minutes * 60
        local = datetime.fromtimestamp(now, ZoneInfo(self.timezone))
        hour, minute = map(int, self.local_time.split(':'))
        candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for _ in range(9):
            if candidate.timestamp() > now and (self.mode == 'daily' or candidate.weekday() == self.weekday):
                return candidate.timestamp()
            candidate += timedelta(days=1)
        raise ValueError('Не удалось рассчитать дату запуска')


class Store:
    def __init__(self, root):
        self.path = root / 'data/schedules.db'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, payload TEXT, enabled INTEGER, next_run REAL, status TEXT, last_run REAL, session TEXT, result TEXT, deleted INTEGER DEFAULT 0)')
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15); db.row_factory = sqlite3.Row; return db

    def list(self):
        with self.connect() as db:
            return [{**json.loads(r['payload']), 'id':r['id'], 'next_run':r['next_run'], 'status':r['status'], 'last_run':r['last_run'], 'session':r['session'], 'result':r['result'], 'enabled':bool(r['enabled'])} for r in db.execute('SELECT * FROM jobs WHERE deleted=0 ORDER BY next_run')]

    @staticmethod
    def signature(row):
        return hashlib.sha256(json.dumps([row[key] for key in ('payload','enabled','next_run','deleted')]).encode()).hexdigest()

    def checked_row(self, db, id_, expected=None):
        row=db.execute('SELECT * FROM jobs WHERE id=? AND deleted=0',(id_,)).fetchone()
        if row is None:raise ValueError('Расписание не найдено')
        if expected is not None and self.signature(row)!=expected:
            raise ValueError('Расписание изменилось. Нужны новый предпросмотр и подтверждение.')
        return row

    def snapshot(self, id_):
        with self.connect() as db:
            row=self.checked_row(db,id_)
            return {'task':{**json.loads(row['payload']),'enabled':bool(row['enabled'])},'signature':self.signature(row),'status':row['status'],'next_run':row['next_run']}

    def save(self, draft, id_=None, *, expected=None, planned_next=None):
        now=time.time(); next_run=planned_next if planned_next is not None else draft.next_time(now, initial=True)
        if draft.enabled and next_run<=now:raise ValueError('Время запуска уже прошло. Подготовьте новое подтверждение.')
        if not draft.enabled:next_run=None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if id_:
                row=self.checked_row(db,id_,expected)
                if row['status']=='running': raise ValueError('Дождитесь завершения текущего запуска')
                db.execute('UPDATE jobs SET payload=?,enabled=?,next_run=? WHERE id=?',(draft.model_dump_json(),draft.enabled,next_run,id_))
            else:
                if db.execute('SELECT count(*) FROM jobs WHERE deleted=0').fetchone()[0]>=20: raise ValueError('Не более 20 расписаний на пользователя')
                id_=uuid4().hex
                db.execute("INSERT INTO jobs VALUES(?,?,?,?,'waiting',NULL,NULL,'',0)",(id_,draft.model_dump_json(),draft.enabled,next_run))
        return id_

    def toggle(self, id_, enabled, *, expected=None, planned_next=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            r=self.checked_row(db,id_,expected)
            draft=Schedule.model_validate_json(r['payload'])
            if enabled and draft.mode=='once': raise ValueError('Создайте новое одноразовое расписание с будущей датой')
            next_run=(planned_next if planned_next is not None else draft.next_time(time.time())) if enabled else None
            if enabled and next_run<=time.time():raise ValueError('Время запуска уже прошло. Подготовьте новое подтверждение.')
            db.execute('UPDATE jobs SET enabled=?,next_run=? WHERE id=?',(enabled,next_run,id_))

    def delete(self,id_, *, expected=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.checked_row(db,id_,expected)
            db.execute('UPDATE jobs SET deleted=1,enabled=0,next_run=NULL WHERE id=?',(id_,))

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted',result='Сервис перезапущен во время выполнения; повтор автоматически не выполняется' WHERE status='running'")

    def claim(self, now):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            r=db.execute("SELECT * FROM jobs WHERE deleted=0 AND enabled=1 AND next_run<=? AND status!='running' ORDER BY next_run LIMIT 1",(now,)).fetchone()
            if r is None: return None
            draft=Schedule.model_validate_json(r['payload']); next_run=draft.next_time(now)
            session=uuid4().hex
            db.execute("UPDATE jobs SET next_run=?,enabled=?,status='running',last_run=?,session=?,result='' WHERE id=?",(next_run,next_run is not None,now,session,r['id']))
            return r['id'],draft,session

    def finish(self,id_,status,result):
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?,result=? WHERE id=?',(status,result[:30000],id_))


def execute_due(service, stop):
    store=Store(service.root);claimed=store.claim(time.time())
    if not claimed:return
    id_,draft,session=claimed;status='ok';result='';deadline=time.monotonic()+900
    class Cancel:
        def is_set(self):return stop.is_set() or time.monotonic()>deadline
    from .integrations.catalog import BUILTINS
    from .integrations.task_writes import WRITE_TOOLS
    def approve(event):
        return draft.allow_reads and event['skill'] in BUILTINS and event['skill'] not in WRITE_TOOLS and event['skill'] != 'ssh_run'
    try:
        run = (service.runner.run(draft.agent,draft.message,session,confirm=approve,cancelled=Cancel(),web_enabled=draft.web_enabled) if draft.agent else service.runner.run_conversation(draft.message,draft.model,session_id=session,confirm=approve,cancelled=Cancel(),web_enabled=draft.web_enabled,connections=draft.connections))
        for event in run:
            if event['type']=='done':result=event.get('content','')
            elif event['type']=='error':status='error';result=event['error']
            elif event['type']=='skill_result' and event.get('status')=='denied':status='needs_confirmation'
        if stop.is_set():status='interrupted'
    except Exception as exc:
        status='error';result=service.audit.redact(str(exc))
    if status=='needs_confirmation':result='Защищённое действие отклонено: требуется подтверждение в чате.\n'+result
    store.finish(id_,status,result)
    service.audit.record(agent=draft.agent or 'plain_chat',kind='schedule',status=status,output={'schedule':id_,'session':session},session_id=session)


class Scheduler:
    def __init__(self, owner, auth):
        self.owner,self.auth=owner,auth;self.stop=threading.Event();self.seen=set()
        self.thread=threading.Thread(target=self.loop,daemon=True,name='agent-scheduler')

    def start(self):self.thread.start()

    def services(self):
        if not self.auth:return [self.owner]
        with self.auth.connect() as db:users=db.execute('SELECT id FROM users WHERE enrolled=1').fetchall()
        # Do not create workspaces for users who have never opened the app.
        return [self.auth.service({'id':uid}) for (uid,) in users if (self.owner.root/'data/users'/uid/'data/schedules.db').exists()]

    def loop(self):
        while not self.stop.is_set():
            try:
                for service in self.services():
                    if self.stop.is_set():break
                    if service.root not in self.seen:
                        Store(service.root).recover();self.seen.add(service.root)
                    execute_due(service,self.stop)
            except Exception:
                logging.getLogger(__name__).exception('Scheduled run failed')
            self.stop.wait(5)

    def close(self):self.stop.set();self.thread.join(timeout=2)


def validate_target(registry, draft):
    if draft.agent:
        registry.build(draft.agent)
    else:
        from .core.providers.factory import make_provider
        from .core.chat_connections import tools_for
        make_provider(draft.model, registry.config)
        tools_for(registry.config,draft.connections)
