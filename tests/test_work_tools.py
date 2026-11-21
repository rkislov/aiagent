import base64
import json
import httpx
import pytest
from pydantic import SecretStr
from test_integrations import mock_http
from superagent.settings import public,update
from superagent.gateway.config import Config


def connection(platform,name,**kwargs):
    cfg=getattr(platform.config.connections,name)
    cfg.enabled=True;cfg.url='https://service.example';cfg.username='reader';cfg.password=SecretStr('private-token')
    for key,value in kwargs.items():setattr(cfg,key,value)
    return cfg


def test_jira_cloud_search_and_read(platform,monkeypatch):
    connection(platform,'jira')
    item={'id':'1','key':'APP-1','fields':{'summary':'Task','description':{'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'Details'}]}]},'status':{'name':'Open'}}}
    def handler(r):
        assert r.method=='GET'
        assert r.headers['authorization']=='Basic '+base64.b64encode(b'reader:private-token').decode()
        if r.url.path.endswith('/search/jql'):
            assert r.url.params['jql']=='project=APP'
            return httpx.Response(200,json={'issues':[item],'nextPageToken':'next'})
        assert r.url.path=='/rest/api/3/issue/APP-1'
        return httpx.Response(200,json=item)
    mock_http(monkeypatch,handler)
    hub=platform.runner.builtins
    result=hub.run('jira_search',{'jql':'project=APP','limit':5,'cursor':''})
    assert result['next_cursor']=='next'
    assert result['issues'][0]['description'].strip()=='Details'
    assert hub.run('jira_issue',{'issue':'APP-1'})['issue']['summary']=='Task'
    with pytest.raises(ValueError):hub.run('jira_issue',{'issue':'../admin'})


def test_jira_server_bearer(platform,monkeypatch):
    connection(platform,'jira',edition='server',auth_mode='bearer')
    def handler(r):
        assert r.url.path=='/rest/api/2/search'
        assert r.url.params['startAt']=='10'
        assert r.headers['authorization']=='Bearer private-token'
        return httpx.Response(200,json={'issues':[{'id':'1','key':'A-1','fields':{}}],'total':12})
    mock_http(monkeypatch,handler)
    assert platform.runner.builtins.run('jira_search',{'jql':'project=A','limit':1,'cursor':'10'})['next_cursor']=='11'


@pytest.mark.parametrize('edition,path',[('cloud','/wiki/api/v2/pages/123'),('server','/rest/api/content/123')])
def test_confluence_page_and_search(platform,monkeypatch,edition,path):
    connection(platform,'confluence',edition=edition)
    def handler(r):
        assert r.method=='GET'
        if r.url.path.endswith('/search'):
            assert r.url.params['cql']=='type=page'
            return httpx.Response(200,json={'results':[{'content':{'id':'123','title':'Guide'},'excerpt':'<b>hello</b>'}],'_links':{'next':'https://evil.example/steal'}})
        assert r.url.path==path
        return httpx.Response(200,json={'id':'123','title':'Guide','body':{'storage':{'value':'<p>Read &amp; learn</p>'}},'version':{'number':2}})
    mock_http(monkeypatch,handler)
    hub=platform.runner.builtins
    assert hub.run('confluence_search',{'cql':'type=page','limit':10,'offset':0})['next_offset']==1
    assert 'Read & learn' in hub.run('confluence_page',{'page_id':'123'})['page']['body']


def test_redmine_filters_and_api_key(platform,monkeypatch):
    connection(platform,'redmine',auth_mode='api_key')
    item={'id':7,'subject':'Fix','status':{'name':'New'},'project':{'id':1,'name':'Work'}}
    def handler(r):
        assert r.headers['X-Redmine-API-Key']=='private-token'
        assert 'authorization' not in r.headers
        assert r.method=='GET'
        return httpx.Response(200,json={'issues':[item],'total_count':1} if r.url.path=='/issues.json' else {'issue':{**item,'journals':[{'notes':'Update'}]}})
    mock_http(monkeypatch,handler)
    hub=platform.runner.builtins
    assert hub.run('redmine_issues',{'filters':{'project_id':1,'status_id':'*'},'limit':5,'offset':0})['total_count']==1
    assert hub.run('redmine_issue',{'issue_id':7})['issue']['journals'][0]['notes']=='Update'
    with pytest.raises(ValueError):hub.run('redmine_issues',{'filters':{'key':'injected'},'limit':5,'offset':0})


def test_bitrix_read_methods_and_pagination(platform,monkeypatch):
    cfg=connection(platform,'bitrix24',username='42')
    task={'id':'8','title':'Read','responsibleId':'42','groupId':'3'}
    methods=[]
    def handler(r):
        assert r.method=='POST'
        assert r.url.path.startswith('/rest/42/private-token/')
        method=r.url.path.rsplit('/',1)[1];methods.append(method)
        body=json.loads(r.content)
        if method=='tasks.task.get.json':
            assert body['taskId']==8
            return httpx.Response(200,json={'result':{'task':task}})
        if method=='crm.lead.list.json':return httpx.Response(200,json={'result':[{'ID':'1','TITLE':'Lead'}]})
        assert body['filter']=={'GROUP_ID':3}
        return httpx.Response(200,json={'result':{'tasks':[task,task]},'next':50})
    mock_http(monkeypatch,handler)
    hub=platform.runner.builtins
    result=hub.run('bitrix24_tasks',{'group_id':3,'limit':1,'start':0})
    assert len(result['tasks'])==1 and result['next_start']==1
    assert hub.run('bitrix24_task',{'task_id':8})['task']['title']=='Read'
    with pytest.raises(ValueError):hub.run('bitrix24_leads',{'limit':5,'start':0})
    cfg.crm_enabled=True
    assert hub.run('bitrix24_leads',{'limit':5,'start':0})['leads'][0]['title']=='Lead'
    assert methods==['tasks.task.list.json','tasks.task.get.json','crm.lead.list.json']


@pytest.mark.parametrize('status',[301,401,403,500])
def test_work_errors_hide_tokens_and_dont_redirect(platform,monkeypatch,status):
    connection(platform,'bitrix24',username='42')
    mock_http(monkeypatch,lambda r:httpx.Response(status,headers={'Location':'https://evil.example'},text='private-token'))
    with pytest.raises(ValueError) as e:platform.runner.builtins.run('bitrix24_task',{'task_id':1})
    assert 'private-token' not in str(e.value)


def test_secret_storage_and_environment_isolation(platform):
    for name in ('jira','confluence','redmine','bitrix24'):
        cfg=connection(platform,name,username='42')
    data=update(platform,public(platform.config))
    assert 'private-token' not in json.dumps(data)
    assert platform.audit.redact('private-token')=='[REDACTED]'
    assert all(data['connections'][name]['password_present'] for name in ('jira','confluence','redmine','bitrix24'))
    assert Config().connections.bitrix24.enabled is False


def test_redmine_acts_as_username_password(platform,monkeypatch):
    connection(platform,'redmine',username='alice')
    def handler(r):
        assert r.headers['authorization']=='Basic '+base64.b64encode(b'alice:private-token').decode()
        assert 'X-Redmine-API-Key' not in r.headers
        assert 'X-Redmine-Switch-User' not in r.headers
        return httpx.Response(200,json={'issues':[],'total_count':0})
    mock_http(monkeypatch,handler)
    assert platform.runner.builtins.run('redmine_issues',{'filters':{},'limit':5,'offset':0})['issues']==[]


def test_redmine_basic_requires_username(platform,monkeypatch):
    connection(platform,'redmine',username='')
    mock_http(monkeypatch,lambda r:pytest.fail('Must not send a request without username'))
    with pytest.raises(ValueError,match='логин'):platform.runner.builtins.run('redmine_issue',{'issue_id':1})
