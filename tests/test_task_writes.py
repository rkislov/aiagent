import json
import httpx
import pytest
from fastapi.testclient import TestClient
from superagent.web.app import create_app
from superagent.integrations.task_writes import WRITE_TOOLS
from test_work_tools import connection
from test_integrations import mock_http
from test_runner import Scripted,call

CASES=[
 ('jira_create_issue',{'fields':{'project_key':'APP','issue_type_id':'1','summary':'New','description':'Line 1\nLine 2'}},'POST','/rest/api/3/issue',201,{'key':'APP-2'}),
 ('jira_update_issue',{'issue_key':'APP-1','fields':{'summary':'Changed'}},'PUT','/rest/api/3/issue/APP-1',204,None),
 ('redmine_create_issue',{'fields':{'project_id':1,'subject':'New'}},'POST','/issues.json',201,{'issue':{'id':2}}),
 ('redmine_update_issue',{'issue_id':1,'fields':{'subject':'Changed'}},'PUT','/issues/1.json',204,None),
 ('bitrix24_create_task',{'fields':{'title':'New','responsible_id':42}},'POST','/rest/42/private-token/tasks.task.add.json',200,{'result':{'task':{'id':'2'}}}),
 ('bitrix24_update_task',{'task_id':1,'fields':{'title':'Changed'}},'POST','/rest/42/private-token/tasks.task.update.json',200,{'result':{'task':{'id':'1'}}}),
]


def setup(platform,monkeypatch,name,payload,write_method,path,status,response,*,changed=False):
    service=name.split('_',1)[0]
    connection(platform,service,username='42')
    reads=[];writes=[]
    def handler(r):
        is_read=r.method=='GET' or r.url.path.endswith('tasks.task.get.json')
        if is_read:
            reads.append(r)
            value='Changed elsewhere' if changed and len(reads)>1 else 'Old'
            if service=='jira':return httpx.Response(200,json={'fields':{'summary':value,'updated':'v1'}})
            if service=='redmine':return httpx.Response(200,json={'issue':{'id':1,'subject':value,'updated_on':'v1'}})
            return httpx.Response(200,json={'result':{'task':{'id':'1','title':value}}})
        writes.append(r)
        assert r.method==write_method and r.url.path==path
        return httpx.Response(status,json=response) if response is not None else httpx.Response(status)
    mock_http(monkeypatch,handler)
    return reads,writes


@pytest.mark.parametrize('case',CASES)
def test_write_plan_single_use_exact_payload(platform,monkeypatch,case):
    name,payload,*_=case
    reads,writes=setup(platform,monkeypatch,*case)
    plan=platform.runner.builtins.prepare_write(name,payload)
    assert not writes
    assert plan.preview['server']=='https://service.example'
    with pytest.raises(PermissionError):plan.execute()
    assert not writes
    preview=plan.preview
    preview['fields'].clear()
    result=plan.execute(confirmed=True)
    assert result['saved'] and len(writes)==1
    assert json.loads(writes[0].content)['fields' if name.startswith(('jira','bitrix')) else 'issue']
    if name=='jira_create_issue':assert json.loads(writes[0].content)['fields']['description']['type']=='doc'
    if name.startswith('redmine'):
        assert writes[0].headers['authorization'].startswith('Basic ')
        assert 'X-Redmine-Switch-User' not in writes[0].headers
    with pytest.raises(PermissionError):plan.execute(confirmed=True)
    assert len(writes)==1


@pytest.mark.parametrize('approved',[None,False,True])
@pytest.mark.parametrize('case',CASES)
def test_agent_gate_without_optional_policy(platform,monkeypatch,approved,case):
    name,payload,*_=case
    reads,writes=setup(platform,monkeypatch,*case)
    agent=platform.registry.agents['office_assistant'];agent.skills=[name];agent.confirmation_required=[]
    seen=[]
    def confirm(approval):
        seen.append(approval)
        assert not writes
        assert approval['preview']['fields']==payload['fields']
        return approved
    events=list(platform.runner.run(agent.name,'Save task',confirm=confirm if approved is not None else None,provider=Scripted([call(name,payload),{'role':'assistant','content':'done'}])))
    assert len(writes)==(1 if approved is True else 0)
    assert next(e for e in events if e['type']=='skill_result')['status']==('ok' if approved else 'denied')
    assert platform.audit.query(skill=name)


@pytest.mark.parametrize('case',[CASES[1],CASES[3],CASES[5]])
def test_change_after_preview_refuses_write(platform,monkeypatch,case):
    reads,writes=setup(platform,monkeypatch,*case,changed=True)
    plan=platform.runner.builtins.prepare_write(case[0],case[1])
    with pytest.raises(ValueError,match='изменилась'):plan.execute(confirmed=True)
    assert not writes


def test_config_snapshot_and_no_direct_http_write(platform,monkeypatch):
    case=CASES[0];reads,writes=setup(platform,monkeypatch,*case)
    plan=platform.runner.builtins.prepare_write(case[0],case[1])
    platform.config.connections.jira.url='https://another.example'
    plan.execute(confirmed=True)
    assert writes[0].url.host=='service.example'
    with pytest.raises(PermissionError):platform.runner.builtins.run(case[0],case[1])
    with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as client:
        assert client.post('/api/connections/jira_create_issue/test',json=case[1]).status_code==400
        assert client.post('/api/skills/test',json={'name':'jira_create_issue','metadata':'name: jira_create_issue\ndescription: fake','code':'def run(input): return {}','input':case[1],'confirmed':True}).status_code==400
    assert len(writes)==1


def test_invalid_fields_no_request(platform,monkeypatch):
    connection(platform,'jira')
    mock_http(monkeypatch,lambda r:pytest.fail('No HTTP allowed'))
    for fields in ({},{'summary':'Only summary'},{'project_key':'A','issue_type_id':'1','summary':'New','confirmed':True}):
        with pytest.raises(ValueError):platform.runner.builtins.prepare_write('jira_create_issue',{'fields':fields})


def test_revocation_before_commit_denies(platform,monkeypatch):
    import threading
    case=CASES[0];name,payload,*_=case
    reads,writes=setup(platform,monkeypatch,*case)
    agent=platform.registry.agents['office_assistant'];agent.skills=[name];agent.confirmation_required=[]
    cancellation=threading.Event()
    def confirm(approval):cancellation.set();return True
    list(platform.runner.run(agent.name,'Save',confirm=confirm,cancelled=cancellation,provider=Scripted([call(name,payload),{'role':'assistant','content':'done'}])))
    assert not writes


def test_cli_refusal_and_acceptance(platform,monkeypatch):
    import sys
    from superagent.cli import main
    case=CASES[0];name,payload,*_=case
    reads,writes=setup(platform,monkeypatch,*case)
    from superagent.settings import update
    update(platform,platform.config.model_dump())
    monkeypatch.setattr(sys,'argv',['superagent','--root',str(platform.root),'skills','test',name,'--input',json.dumps(payload)])
    monkeypatch.setattr('builtins.input',lambda _: 'n')
    with pytest.raises(SystemExit):main()
    assert not writes
    monkeypatch.setattr('builtins.input',lambda _: 'y')
    main()
    assert len(writes)==1


def test_write_timeout_not_retried(platform,monkeypatch):
    connection(platform,'jira')
    sent=[]
    def handler(r):
        sent.append(r)
        raise httpx.ReadTimeout('private-token',request=r)
    mock_http(monkeypatch,handler)
    plan=platform.runner.builtins.prepare_write(CASES[0][0],CASES[0][1])
    with pytest.raises(ValueError,match='неизвестен') as error:plan.execute(confirmed=True)
    assert 'private-token' not in str(error.value)
    assert len(sent)==1
    with pytest.raises(PermissionError):plan.execute(confirmed=True)


def test_jira_server_description_stays_text(platform,monkeypatch):
    connection(platform,'jira',username='42',edition='server')
    def handler(r):
        assert r.url.path=='/rest/api/2/issue'
        assert isinstance(json.loads(r.content)['fields']['description'],str)
        return httpx.Response(201,json={'key':'APP-2'})
    mock_http(monkeypatch,handler)
    assert platform.runner.builtins.prepare_write(CASES[0][0],CASES[0][1]).execute(confirmed=True)['saved']
