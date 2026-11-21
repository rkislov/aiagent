import json
import httpx
import pytest
from fastapi.testclient import TestClient
from superagent.web.app import create_app
from superagent.gateway.agent_templates import work_manager
from test_work_tools import connection
from test_integrations import mock_http
from test_runner import Scripted,call


@pytest.mark.parametrize('service,project,assignee,kind,skill',[
 ('jira','APP','account:1','10001','jira_create_issue'),('redmine','12','3','','redmine_create_issue'),('bitrix24','4','42','','bitrix24_create_task')])
def test_dispatch_never_writes(platform,monkeypatch,service,project,assignee,kind,skill):
    mock_http(monkeypatch,lambda r:pytest.fail('Dispatcher must not access HTTP'))
    p={'service':service,'project':project,'assignee':assignee,'issue_type_id':kind,'message':'Подготовить отчёт до 2026-12-01','summary':'Отчёт','due_date':''}
    result=platform.runner.builtins.run('task_dispatch',p)['proposal']
    assert result['ready'] and not result['saved'] and result['needs_confirmation']
    assert result['skill']==skill and result['date_candidates']==['2026-12-01']
    assert result['input']['fields']['description']==p['message']
    assert 'due_date' not in result['input']['fields']


def test_dispatch_missing_ids_and_input_bounds(platform):
    p={'service':'jira','project':'','assignee':'','issue_type_id':'','message':'Новая задача','summary':'','due_date':''}
    result=platform.runner.builtins.run('task_dispatch',p)['proposal']
    assert not result['ready'] and set(result['missing_fields'])=={'project_key','issue_type_id'}
    with pytest.raises(ValueError):platform.runner.builtins.run('task_dispatch',{**p,'message':''})


def test_stalled_jira_pagination_unknown_dates_and_done(platform,monkeypatch):
    connection(platform,'jira')
    requested=[]
    def handler(r):
        assert r.method=='GET';requested.append(r)
        assert 'statusCategory != Done' in r.url.params['jql']
        def item(key,updated,due='',category='indeterminate'):
            return {'key':key,'fields':{'summary':key,'updated':updated,'duedate':due,'status':{'name':'In progress','statusCategory':{'key':category}}}}
        if 'nextPageToken' not in r.url.params:
            return httpx.Response(200,json={'issues':[item('APP-1','2026-11-01T00:00:00Z','2026-11-15')],'nextPageToken':'page2'})
        return httpx.Response(200,json={'issues':[item('APP-2','','2026-11-21'),item('APP-3','2026-10-01T00:00:00Z','','done')]})
    mock_http(monkeypatch,handler)
    report=platform.runner.builtins.run('stalled_tasks',{'service':'jira','project':'APP','limit':100,'stale_days':7,'as_of':'2026-11-21T12:00:00+03:00'})['stalled']
    assert len(requested)==2 and report['examined']==3 and not report['truncated']
    assert [t['key'] for t in report['tasks']]==['APP-1']
    assert set(report['tasks'][0]['reasons'])=={'no_updates','overdue'}
    assert report['unknown_update_time']==['APP-2']


def test_stalled_redmine_truncation(platform,monkeypatch):
    connection(platform,'redmine')
    def handler(r):
        assert r.url.params['status_id']=='open'
        return httpx.Response(200,json={'issues':[{'id':1,'subject':'Overdue','due_date':'2026-11-20','updated_on':'2026-11-21T00:00:00Z'}],'total_count':50})
    mock_http(monkeypatch,handler)
    report=platform.runner.builtins.run('stalled_tasks',{'service':'redmine','project':'1','limit':1,'stale_days':7,'as_of':'2026-11-21T00:00:00+03:00'})['stalled']
    assert report['truncated'] and report['tasks'][0]['reasons']==['overdue']


@pytest.mark.parametrize('service,release',[('jira','1.2.3'),('redmine','12')])
def test_release_notes_readonly_and_linked(platform,monkeypatch,service,release):
    connection(platform,service)
    def handler(r):
        assert r.method=='GET'
        if service=='jira':
            assert 'fixVersion = "1.2.3"' in r.url.params['jql'] and 'statusCategory = Done' in r.url.params['jql']
            data={'issues':[{'id':'1','key':'APP-1','fields':{'summary':'Fixed [link](evil)','issuetype':{'name':'Bug'}}}]}
        else:
            assert r.url.params['fixed_version_id']=='12' and r.url.params['status_id']=='closed'
            data={'issues':[{'id':1,'subject':'Fixed','tracker':{'name':'Bug'}}],'total_count':1}
        return httpx.Response(200,json=data)
    mock_http(monkeypatch,handler)
    notes=platform.runner.builtins.run('release_notes',{'service':service,'project':'APP' if service=='jira' else '1','release':release,'limit':100})['notes']
    assert notes['draft'] and not notes['published'] and not notes['truncated']
    assert 'https://service.example/' in notes['markdown']
    assert '## Bug' in notes['markdown']
    if service=='jira':assert '\\[link\\]' in notes['markdown']


def test_template_build_and_auth(platform):
    agent=work_manager();platform.registry.build(agent.name,agent)
    assert {'task_dispatch','stalled_tasks','release_notes'}<=set(agent.skills)
    with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as c:
        assert c.get('/api/agent-templates/work_manager').json()['name']=='work_manager'
    with TestClient(create_app(platform),base_url='http://localhost') as c:
        assert c.get('/api/agent-templates/work_manager').status_code==401


def test_proposal_to_creation_still_requires_second_confirmation(platform,monkeypatch):
    connection(platform,'redmine')
    mock_http(monkeypatch,lambda r:pytest.fail('Refused creation must never be sent'))
    agent=work_manager();agent.skills=['task_dispatch','redmine_create_issue'];agent.confirmation_required=[]
    platform.registry.agents[agent.name]=agent
    dispatch={'service':'redmine','project':'1','assignee':'','issue_type_id':'','message':'Создать отчёт','summary':'Отчёт','due_date':''}
    responses=Scripted([call('task_dispatch',dispatch),call('redmine_create_issue',{'fields':{'project_id':'1','subject':'Отчёт','description':'Создать отчёт'}}),{'role':'assistant','content':'Отказано'}])
    prompts=[]
    def confirm(p):prompts.append(p['skill']);return p['skill']=='task_dispatch'
    events=list(platform.runner.run(agent.name,'Предложи и создай',confirm=confirm,provider=responses))
    assert prompts==['task_dispatch','redmine_create_issue']
    assert next(e for e in events if e['type']=='skill_result' and e['skill']=='redmine_create_issue')['status']=='denied'


def test_workflow_skills_combine_with_existing_office_skills(platform):
    agent=platform.registry.agents['office_assistant'].model_copy(deep=True)
    agent.skills.extend(['task_dispatch','stalled_tasks','release_notes'])
    assert platform.registry.build(agent.name,agent).name==agent.name
