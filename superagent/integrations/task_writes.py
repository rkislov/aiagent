"""Immutable, single-use task write plans, executed only after human approval."""
import json
import re
import threading
from datetime import date, datetime
from urllib.parse import quote
from .work_tools import numeric

WRITE_TOOLS = {'schedule_save','schedule_control','confluence_publish_release','jira_create_issue','jira_update_issue','redmine_create_issue','redmine_update_issue','bitrix24_create_task','bitrix24_update_task'}


def validate_fields(service, fields, create):
    if not isinstance(fields,dict) or not fields or len(json.dumps(fields,ensure_ascii=False))>50000:
        raise ValueError('Нужен непустой объект fields размером до 50 KB')
    allowed={
        'jira':{'summary','description','assignee_id','priority_id','due_date','labels'} | ({'project_key','issue_type_id'} if create else set()),
        'redmine':{'subject','description','tracker_id','status_id','priority_id','assigned_to_id','due_date','start_date','done_ratio','notes'} | ({'project_id'} if create else set()),
        'bitrix24':{'title','description','responsible_id','group_id','deadline','priority'},
    }[service]
    if set(fields)-allowed: raise ValueError('Недопустимые поля: '+', '.join(sorted(set(fields)-allowed)))
    required={'jira':{'project_key','issue_type_id','summary'},'redmine':{'project_id','subject'},'bitrix24':{'title','responsible_id'}}[service] if create else set()
    if required-set(fields): raise ValueError('Обязательные поля: '+', '.join(sorted(required)))
    for key,value in fields.items():
        if key in ('summary','subject','title','description','notes'):
            if not isinstance(value,str) or len(value)>20000 or (key in ('summary','subject','title') and not value.strip()): raise ValueError('Некорректное текстовое поле '+key)
        elif key in ('due_date','start_date'):
            if not isinstance(value,str): raise ValueError('Дата должна быть строкой ISO YYYY-MM-DD')
            if value: date.fromisoformat(value)
        elif key=='deadline':
            if not isinstance(value,str): raise ValueError('deadline должен быть строкой ISO')
            if value and not datetime.fromisoformat(value).tzinfo:raise ValueError('deadline требует часовой пояс')
        elif key=='labels':
            if not isinstance(value,list) or len(value)>50 or any(not isinstance(v,str) or not re.fullmatch(r'[\w-]{1,100}',v) for v in value):raise ValueError('Некорректные labels')
        elif key in ('project_key','project_id'):
            if type(value) not in (str,int) or not re.fullmatch(r'[\w-]{1,100}',str(value)):raise ValueError('Некорректный проект')
        elif key in ('done_ratio','priority'):
            maximum=100 if key=='done_ratio' else 2
            if type(value) is not int or not 0<=value<=maximum:raise ValueError('Некорректное значение '+key)
        elif key=='group_id' and type(value) is int and value==0: pass
        elif key=='assignee_id':
            if value is not None and (not isinstance(value,str) or not re.fullmatch(r'[\w:.-]{1,200}',value)):raise ValueError('Некорректный assignee_id')
        else: numeric(value)
    return json.loads(json.dumps(fields))


def adf(value):
    return {'type':'doc','version':1,'content':[{'type':'paragraph','content':[{'type':'text','text':line}] if line else []} for line in value.split('\n')]}


class WritePlan:
    def __init__(self,hub,name,payload):
        self.hub=hub
        self.name=name
        self.service=name.split('_',1)[0]
        self.create='_create_' in name
        self.cfg=getattr(hub.config.connections,self.service)
        if not self.cfg.enabled:raise ValueError('Подключение выключено')
        if not self.cfg.key():raise ValueError('Не заданы учётные данные')
        fields=validate_fields(self.service,payload['fields'],self.create)
        self.target='new' if self.create else str(payload['issue_key'] if self.service=='jira' else payload['issue_id'] if self.service=='redmine' else payload['task_id'])
        if not self.create:
            if self.service=='jira':
                if not re.fullmatch(r'(?:[A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*|[1-9][0-9]*)',self.target):raise ValueError('Некорректный ключ Jira')
            else:numeric(self.target)
        api={}
        if self.service=='jira':
            for key,value in fields.items():
                if key=='project_key':api['project']={'key':str(value)}
                elif key=='issue_type_id':api['issuetype']={'id':str(value)}
                elif key=='description':api[key]=adf(value) if self.cfg.edition=='cloud' else value
                elif key=='assignee_id':api['assignee']=None if value is None else {('accountId' if self.cfg.edition=='cloud' else 'name'):value}
                elif key=='priority_id':api['priority']={'id':str(value)}
                elif key=='due_date':api['duedate']=value or None
                else:api[key]=value
        elif self.service=='bitrix24':api={key.upper():value for key,value in fields.items()}
        else:api=fields
        self.api_json=json.dumps(api,ensure_ascii=False,sort_keys=True)
        self.original=None if self.create else self.read_current()
        before=self.original
        self._preview_json=json.dumps({'service':self.service,'server':self.cfg.url,'action':'create' if self.create else 'update','target':self.target,'fields':fields,'request_fields':api,'current':before},ensure_ascii=False)
        self.used=False
        self.lock=threading.Lock()

    @property
    def preview(self):return json.loads(self._preview_json)

    def read_current(self):
        if self.service=='jira':
            version='3' if self.cfg.edition=='cloud' else '2'
            data=self.hub._work_request(self.cfg,f'/rest/api/{version}/issue/'+quote(self.target,safe=''),params={'fields':','.join([*json.loads(self.api_json),'updated'])})
            return data.get('fields',{})
        if self.service=='redmine':
            item=self.hub._work_request(self.cfg,'/issues/'+self.target+'.json',redmine=True)['issue']
            return {k:v for k,v in item.items() if k not in ('journals','attachments','relations','watchers')}
        return self.hub._bitrix(self.cfg,'tasks.task.get',{'taskId':int(self.target)})['result']['task']

    def execute(self,*,confirmed=False):
        if confirmed is not True:raise PermissionError('Требуется явное подтверждение записи')
        with self.lock:
            if self.used:raise PermissionError('Подтверждение уже использовано')
            self.used=True
        if not self.create and self.read_current()!=self.original:
            raise ValueError('Задача изменилась после предпросмотра. Нужны новый запрос и подтверждение.')
        api=json.loads(self.api_json)
        if self.service=='jira':
            version='3' if self.cfg.edition=='cloud' else '2'
            path=f'/rest/api/{version}/issue'+('' if self.create else '/'+quote(self.target,safe=''))
            result=self.hub._work_request(self.cfg,path,body={'fields':api},method='POST' if self.create else 'PUT',writing=True)
            identity=str(result.get('key',result.get('id',self.target)))
            if self.create and identity in ('','new','None','True','False'):raise ValueError('Сервис не вернул ID созданной задачи; проверьте результат перед повтором')
            return {'saved':True,'id':identity,'url':self.cfg.url+'/browse/'+quote(identity,safe='')}
        if self.service=='redmine':
            result=self.hub._work_request(self.cfg,'/issues'+('.json' if self.create else '/'+self.target+'.json'),body={'issue':api},method='POST' if self.create else 'PUT',redmine=True,writing=True)
            identity=str(result.get('issue',{}).get('id',self.target))
            if self.create and identity in ('','new','None','True','False'):raise ValueError('Сервис не вернул ID созданной задачи; проверьте результат перед повтором')
            return {'saved':True,'id':identity,'url':self.cfg.url+'/issues/'+identity}
        result=self.hub._bitrix(self.cfg,'tasks.task.add' if self.create else 'tasks.task.update',{'fields':api,**({} if self.create else {'taskId':int(self.target)})},writing=True)
        result=result.get('result')
        if result is False or result is None:raise ValueError('Bitrix24 не подтвердил сохранение')
        task=result.get('task',{}) if isinstance(result,dict) else {}
        identity=str(task.get('id',self.target if not self.create else result.get('id','') if isinstance(result,dict) else result))
        if self.create and identity in ('','new','None','True','False'):raise ValueError('Сервис не вернул ID созданной задачи; проверьте результат перед повтором')
        return {'saved':True,'id':identity,'url':self.cfg.url+'/company/personal/user/'+self.cfg.username+'/tasks/task/view/'+quote(identity,safe='')+'/'}
