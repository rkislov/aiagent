"""Fixed task and knowledge-system adapters. Writes use approved plans."""
import html
import json
import re
from urllib.parse import quote
import httpx

WORK_TOOLS = {'jira_search','jira_issue','confluence_search','confluence_page','redmine_issues','redmine_issue','bitrix24_tasks','bitrix24_task','bitrix24_leads'}


def text(value):
    if isinstance(value,dict):
        return (' '.join([str(value.get('text',''))]+[text(c) for c in value.get('content',[])]) if isinstance(value.get('content',[]),list) else '')[:12000]
    return html.unescape(re.sub(r'<[^>]*>',' ',str(value or '')))[:12000]


def bounded(value, minimum=0, maximum=10000):
    if type(value) is not int or not minimum<=value<=maximum: raise ValueError('Числовой параметр вне допустимого диапазона')
    return value


def numeric(value):
    value=str(value)
    if not re.fullmatch(r'[1-9][0-9]{0,19}',value): raise ValueError('Нужен положительный числовой ID')
    return value


def query(value):
    if not value.strip() or len(value)>4000: raise ValueError('Нужен поисковый запрос длиной до 4000 символов')
    return value


class WorkTools:
    def _work_request(self,cfg,path,*,params=None,body=None,base=None,redmine=False,webhook=False,method=None,writing=False):
        if not cfg.key(): raise ValueError('Укажите токен или пароль в настройках подключения')
        headers={'Accept':'application/json'}
        auth=None
        if redmine:
            if cfg.auth_mode=='api_key': headers['X-Redmine-API-Key']=cfg.key()
            else:
                if not cfg.username: raise ValueError('Для Redmine укажите логин пользователя')
                auth=(cfg.username,cfg.key())
        elif not webhook:
            if cfg.auth_mode=='basic':
                if not cfg.username: raise ValueError('Для Basic auth укажите логин/email')
                auth=(cfg.username,cfg.key())
            else: headers['Authorization']='Bearer '+cfg.key()
        try:
            with httpx.Client(timeout=20,trust_env=False,follow_redirects=False,auth=auth,headers=headers) as client:
                with client.stream(method or ('POST' if body is not None else 'GET'),(base or cfg.url).rstrip('/')+path,params=params,json=body) as response:
                    if writing and response.status_code==409:raise ValueError('Конфликт версии; обновите данные и запросите новое подтверждение')
                    if response.status_code not in ((200,201,204) if writing else (200,)):
                        raise ValueError(f'Сервис вернул HTTP {response.status_code}; проверьте адрес, токен и права')
                    raw=bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw)>2_000_000: raise ValueError('Ответ превышает 2 MB; уменьшите выборку')
            if writing and response.status_code==204:return {}
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError('Некорректный JSON-ответ сервиса')
            if data.get('error'):raise ValueError('Сервис отклонил запрос; проверьте права и параметры')
            return data
        except (httpx.HTTPError,UnicodeError,json.JSONDecodeError):
            raise ValueError('Результат записи неизвестен; проверьте задачу в сервисе перед повтором' if writing else 'Не удалось прочитать данные сервиса: ошибка сети или ответа') from None

    def _jira_item(self,cfg,item):
        fields=item.get('fields',{})
        return {'id':str(item.get('id','')),'key':item.get('key',''),'summary':text(fields.get('summary')),'description':text(fields.get('description')),'status':(fields.get('status') or {}).get('name',''),'assignee':(fields.get('assignee') or {}).get('displayName',''),'project':(fields.get('project') or {}).get('key',''),'status_category':(fields.get('status') or {}).get('statusCategory',{}).get('key',''),'due_date':fields.get('duedate',''),'issue_type':(fields.get('issuetype') or {}).get('name',''),'updated':fields.get('updated',''),'url':cfg.url+'/browse/'+quote(item.get('key',''),safe='')}

    def jira_search(self,cfg,p):
        params={'jql':query(p['jql']),'maxResults':p['limit'],'fields':'summary,description,status,assignee,project,updated,duedate,issuetype'}
        if len(p['cursor'])>4000:raise ValueError('Слишком длинный cursor')
        if cfg.edition=='cloud':
            path='/rest/api/3/search/jql'
            if p['cursor']:params['nextPageToken']=p['cursor']
        else:
            path='/rest/api/2/search'
            try:offset=int(p['cursor'] or '0')
            except ValueError:raise ValueError('Для Jira Server cursor должен быть числовым') from None
            params['startAt']=bounded(offset)
        data=self._work_request(cfg,path,params=params)
        issues=data.get('issues',[])
        next_cursor=data.get('nextPageToken','') if cfg.edition=='cloud' else str(params['startAt']+len(issues)) if params['startAt']+len(issues)<data.get('total',0) else ''
        return {'issues':[self._jira_item(cfg,x) for x in issues[:p['limit']]],'next_cursor':next_cursor}

    def jira_issue(self,cfg,p):
        if not re.fullmatch(r'(?:[A-Za-z][A-Za-z0-9_]*-[1-9][0-9]*|[1-9][0-9]*)',p['issue']):raise ValueError('Некорректный ключ задачи Jira')
        version='3' if cfg.edition=='cloud' else '2'
        item=self._work_request(cfg,f'/rest/api/{version}/issue/'+quote(p['issue'],safe=''),params={'fields':'summary,description,status,assignee,project,updated,duedate,issuetype'})
        return {'issue':self._jira_item(cfg,item)}

    def _confluence_base(self,cfg):
        return cfg.url if cfg.edition=='server' or cfg.url.endswith('/wiki') else cfg.url+'/wiki'

    def confluence_search(self,cfg,p):
        offset=bounded(p['offset'])
        data=self._work_request(cfg,'/rest/api/search',base=self._confluence_base(cfg),params={'cql':query(p['cql']),'limit':p['limit'],'start':offset})
        rows=data.get('results',[])[:p['limit']]
        pages=[]
        for row in rows:
            content=row.get('content',row)
            pages.append({'id':str(content.get('id','')),'title':text(content.get('title',row.get('title',''))),'excerpt':text(row.get('excerpt','')),'type':content.get('type',''),'url':self._confluence_base(cfg)+'/pages/viewpage.action?pageId='+quote(str(content.get('id','')),safe='')})
        more=bool(data.get('_links',{}).get('next'))
        return {'pages':pages,'next_offset':offset+len(rows) if more else -1}

    def confluence_page(self,cfg,p):
        page_id=numeric(p['page_id'])
        base=self._confluence_base(cfg)
        if cfg.edition=='cloud':
            data=self._work_request(cfg,'/api/v2/pages/'+page_id,base=base,params={'body-format':'storage'})
        else:
            data=self._work_request(cfg,'/rest/api/content/'+page_id,base=base,params={'expand':'body.storage,version,space'})
        return {'page':{'id':str(data.get('id',page_id)),'title':text(data.get('title','')),'body':text(data.get('body',{}).get('storage',{}).get('value','')),'version':data.get('version',{}).get('number',0),'space_id':str(data.get('spaceId','')),'space_key':data.get('space',{}).get('key',''),'url':base+'/pages/viewpage.action?pageId='+page_id}}

    def _redmine_item(self,cfg,item):
        return {'id':item['id'],'subject':text(item.get('subject')),'description':text(item.get('description')),'project':item.get('project',{}),'status':item.get('status',{}).get('name',''),'assigned_to':item.get('assigned_to',{}).get('name',''),'updated':item.get('updated_on',''),'closed_on':item.get('closed_on',''),'issue_type':item.get('tracker',{}).get('name',''),'due_date':item.get('due_date',''),'url':cfg.url+'/issues/'+str(item['id'])}

    def redmine_issues(self,cfg,p):
        allowed={'project_id','status_id','assigned_to_id','tracker_id','subject','fixed_version_id'}
        if set(p['filters'])-allowed or any(not isinstance(v,(str,int)) or len(str(v))>1000 for v in p['filters'].values()):raise ValueError('Недопустимые фильтры Redmine')
        params={**p['filters'],'limit':p['limit'],'offset':bounded(p['offset'])}
        data=self._work_request(cfg,'/issues.json',params=params,redmine=True)
        return {'issues':[self._redmine_item(cfg,x) for x in data.get('issues',[])[:p['limit']]],'total_count':int(data.get('total_count',0))}

    def redmine_issue(self,cfg,p):
        data=self._work_request(cfg,'/issues/'+numeric(p['issue_id'])+'.json',params={'include':'journals'},redmine=True)
        item=data['issue'];result=self._redmine_item(cfg,item)
        result['journals']=[{'created_on':j.get('created_on',''),'notes':text(j.get('notes')),'user':j.get('user',{}).get('name','')} for j in item.get('journals',[])[:100]]
        return {'issue':result}

    def _bitrix(self,cfg,method,body,*,writing=False):
        if not cfg.username.isdigit() or not re.fullmatch(r'[A-Za-z0-9_-]+',cfg.key()):raise ValueError('Проверьте ID пользователя и токен входящего webhook')
        return self._work_request(cfg,f'/rest/{cfg.username}/{cfg.key()}/{method}.json',body=body,webhook=True,writing=writing)

    def _bitrix_task(self,cfg,item):
        return {'id':str(item.get('id','')),'title':text(item.get('title')),'description':text(item.get('description')),'status':str(item.get('status','')),'deadline':item.get('deadline',''),'responsible_id':str(item.get('responsibleId','')),'group_id':str(item.get('groupId','')),'url':cfg.url+'/company/personal/user/'+quote(str(item.get('responsibleId','0')),safe='')+'/tasks/task/view/'+quote(str(item.get('id','')),safe='')+'/'}

    def bitrix24_tasks(self,cfg,p):
        filters={}
        if bounded(p['group_id'],maximum=2147483647):filters['GROUP_ID']=p['group_id']
        start=bounded(p['start'],maximum=1000000)
        data=self._bitrix(cfg,'tasks.task.list',{'filter':filters,'select':['ID','TITLE','DESCRIPTION','STATUS','DEADLINE','RESPONSIBLE_ID','GROUP_ID'],'order':{'ID':'ASC'},'start':start})
        rows=data.get('result',{}).get('tasks',[])
        next_start=start+p['limit'] if len(rows)>p['limit'] else int(data.get('next',-1))
        return {'tasks':[self._bitrix_task(cfg,x) for x in rows[:p['limit']]],'next_start':next_start}

    def bitrix24_task(self,cfg,p):
        data=self._bitrix(cfg,'tasks.task.get',{'taskId':int(numeric(p['task_id']))})
        return {'task':self._bitrix_task(cfg,data['result']['task'])}

    def bitrix24_leads(self,cfg,p):
        if not cfg.crm_enabled:raise ValueError('Чтение лидов CRM нужно отдельно включить в подключении Bitrix24')
        start=bounded(p['start'],maximum=1000000)
        data=self._bitrix(cfg,'crm.lead.list',{'select':['ID','TITLE','STATUS_ID','ASSIGNED_BY_ID','DATE_CREATE'],'order':{'ID':'ASC'},'start':start})
        rows=data.get('result',[])
        next_start=start+p['limit'] if len(rows)>p['limit'] else int(data.get('next',-1))
        return {'leads':[{'id':str(x['ID']),'title':text(x.get('TITLE')),'status':x.get('STATUS_ID',''),'assigned_to':x.get('ASSIGNED_BY_ID',''),'created':x.get('DATE_CREATE',''),'url':cfg.url+'/crm/lead/details/'+str(x['ID'])+'/'} for x in rows[:p['limit']]],'next_start':next_start}
