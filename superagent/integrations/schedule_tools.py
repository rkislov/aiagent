"""Workspace-scoped schedule tools with immutable, single-use confirmation plans."""
import json
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from superagent.schedules import Schedule, Store, validate_target

SCHEDULE_TOOLS = {'schedule_list','schedule_save','schedule_control'}


def context(hub):
    if hub.root is None or hub.registry is None:
        raise ValueError('Планировщик доступен только в пространстве пользователя')
    return Store(hub.root)


def local_time(timestamp, timezone):
    return datetime.fromtimestamp(timestamp,ZoneInfo(timezone)).isoformat() if timestamp is not None else ''


def list_schedules(hub):
    store=context(hub)
    hub.registry.scan()
    jobs=[]
    for row in store.list():
        jobs.append({k:v for k,v in row.items() if k not in ('result','session')})
        jobs[-1]['next_run_local']=local_time(row['next_run'],row['timezone'])
    from superagent.core.providers.pool import effective_providers
    from superagent.core.providers.capabilities import supports_chat
    models=[f'{name}:{model}' for name,p in effective_providers(hub.config).items() if p.key() or p.type in ('demo','ollama') for model in p.models if supports_chat(p,model)]
    return {'schedules':jobs,'agents':[{'name':a.name,'model':a.model,'skills':a.skills} for a in hub.registry.agents.values()], 'models':models,'timezone':'Europe/Moscow'}


class SchedulePlan:
    def __init__(self,hub,name,payload,*,model=None):
        self.store=context(hub)
        self.registry=hub.registry
        self.registry.scan()
        p=json.loads(json.dumps(payload))
        self.name=name
        self.id=p['schedule_id']
        if self.id and not re.fullmatch(r'[a-f0-9]{32}',self.id):raise ValueError('Нужен точный ID расписания из schedule_list')
        self.expected=None
        self.next_run=None
        self.used=False
        self.lock=threading.Lock()
        before=self.store.snapshot(self.id) if self.id else None
        if before:self.expected=before['signature']
        if name=='schedule_save':
            if not p['task'].get('agent') and 'model' not in p['task']:
                if not model:raise ValueError('Для задания без агента укажите model')
                p['task']['model']=model
            draft=Schedule.model_validate(p['task'])
            validate_target(self.registry,draft)
            first_run=draft.next_time(time.time(),initial=True)
            self.next_run=first_run if draft.enabled else None
            if before and before['status']=='running':raise ValueError('Дождитесь завершения текущего запуска')
            self.task_json=draft.model_dump_json()
            operation='update' if self.id else 'create'
            preview={'operation':operation,'schedule_id':self.id,'task':json.loads(self.task_json),'next_run_local':local_time(self.next_run,draft.timezone),'automatic_source_reading':draft.allow_reads,'web_reading':draft.web_enabled}
            if before:preview['before']=before['task']
        else:
            if not self.id:raise ValueError('Укажите ID расписания из schedule_list')
            action=p['action']
            if action not in ('pause','resume','delete'):raise ValueError('action: pause, resume или delete')
            self.action=action
            draft=Schedule.model_validate(before['task'])
            if action=='resume':
                if draft.mode=='once':raise ValueError('Создайте новое одноразовое расписание с будущей датой')
                validate_target(self.registry,draft)
                self.next_run=draft.next_time(time.time())
            preview={'operation':action,'schedule_id':self.id,'task':before['task'],'next_run_local':local_time(self.next_run,draft.timezone)}
        preview['notice']='Запись данных, SSH и изменение расписаний не подтверждаются автоматически в фоновых заданиях. Пауза и удаление отключают будущие запуски; начатый запуск продолжается. Результаты сохраняются в разделе «Расписание» и истории диалогов.'
        self.preview_json=json.dumps(preview,ensure_ascii=False)

    @property
    def preview(self):return json.loads(self.preview_json)

    def execute(self,*,confirmed=False):
        if confirmed is not True:raise PermissionError('Изменение расписания требует явного подтверждения')
        with self.lock:
            if self.used:raise PermissionError('Подтверждение уже использовано')
            self.used=True
        if self.name=='schedule_save':
            draft=Schedule.model_validate_json(self.task_json)
            self.registry.scan()
            validate_target(self.registry,draft)
            id_=self.store.save(draft,self.id or None,expected=self.expected,planned_next=self.next_run)
            return {'saved':True,'schedule_id':id_,'operation':'update' if self.id else 'create','enabled':draft.enabled,'next_run':local_time(self.next_run,draft.timezone)}
        if self.action=='resume':
            self.registry.scan()
            validate_target(self.registry,Schedule.model_validate(self.preview['task']))
        if self.action=='delete':self.store.delete(self.id,expected=self.expected)
        else:self.store.toggle(self.id,self.action=='resume',expected=self.expected,planned_next=self.next_run)
        return {'saved':True,'schedule_id':self.id,'operation':self.action,'enabled':self.action=='resume','next_run':self.preview['next_run_local']}
