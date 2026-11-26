import copy
import json
import time
from fastapi.testclient import TestClient
from superagent.web.app import create_app
from superagent.core.conversations import import_agent_history
from superagent.integrations.schedule_tools import SCHEDULE_TOOLS
CHAT_UTILITIES=SCHEDULE_TOOLS|{'dialog_search','office_list','office_read','office_create','office_edit','office_compare','image_generate'}


class Recorder:
    def __init__(self):self.calls=[]
    def chat(self,messages,tools):
        self.calls.append((copy.deepcopy(messages),copy.deepcopy(tools)))
        return {'role':'assistant','content':'ready'}


def test_attach_detach_preserves_history_and_removes_tools(platform):
    provider=Recorder()
    events=list(platform.runner.run_conversation('Remember secret-context-123','demo:office',provider=provider))
    session=events[0]['session_id']
    assert {t['function']['name'] for t in provider.calls[-1][1]}==CHAT_UTILITIES
    list(platform.runner.run_conversation('Use that context','demo:office',attached_agent='office_assistant',session_id=session,provider=provider))
    assert 'secret-context-123' in json.dumps(provider.calls[-1][0])
    assert 'email_filter' in {t['function']['name'] for t in provider.calls[-1][1]}
    list(platform.runner.run_conversation('Continue without agent','demo:office',session_id=session,provider=provider))
    assert {t['function']['name'] for t in provider.calls[-1][1]}==CHAT_UTILITIES
    assert 'Use that context' in json.dumps(provider.calls[-1][0])
    assert not any(k.startswith('_') for m in provider.calls[-1][0] for k in m)
    history=platform.runner.conversation_memory().load(session)
    assert [m['_speaker'] for m in history if m['role']=='assistant']==['plain_chat','office_assistant','plain_chat']
    assert len(platform.runner.conversation_memory().sessions('__conversation__'))==1
    assert platform.runner.memory(platform.registry.agents['office_assistant']).sessions('office_assistant')==[]


def test_legacy_conversation_can_be_continued_without_agent(platform):
    provider=Recorder()
    events=list(platform.runner.run('office_assistant','legacy-context',provider=provider))
    old_id=events[0]['session_id']
    new_id=import_agent_history(platform,'office_assistant',old_id)
    list(platform.runner.run_conversation('continue','demo:office',session_id=new_id,provider=provider))
    assert 'legacy-context' in json.dumps(provider.calls[-1][0])
    assert len(platform.runner.memory(platform.registry.agents['office_assistant']).load(old_id))==2
    assert len(platform.runner.conversation_memory().load(new_id))==4


def test_poll_delivers_confirmation_before_action_is_approved(platform,monkeypatch):
    class Provider:
        def chat(self,messages,tools):
            if any(m['role']=='tool' for m in messages):return {'role':'assistant','content':'declined'}
            return {'role':'assistant','content':None,'tool_calls':[{'id':'call_test','type':'function','function':{'name':'imap_read','arguments':json.dumps({'folder':'INBOX','query':'ALL','limit':1})}}]}
    agent=platform.registry.agents['office_assistant'];agent.skills=['imap_read'];agent.confirmation_required=[]
    monkeypatch.setattr('superagent.core.agent_loop.make_provider',lambda *a:Provider())
    # create_app does not re-scan this registry during the request.
    with TestClient(create_app(platform,authentication=False),base_url="http://localhost") as client:
        response=client.post('/api/chat',json={'agent':agent.name,'message':'read mail','transport':'poll'})
        assert response.status_code==200,response.text
        run=response.json()['run_id'];events=[]
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            data=client.get('/api/chat/runs/'+run).json();events+=data['events']
            if any(e['type']=='confirmation' for e in events):break
            time.sleep(.01)
        approval=next(e for e in events if e['type']=='confirmation')
        assert client.post('/api/confirmations/'+approval['id'],json={'approved':False}).status_code==200
        while time.monotonic()<deadline:
            data=client.get('/api/chat/runs/'+run).json();events+=data['events']
            if data['done']:break
            time.sleep(.01)
        assert any(e['type']=='done' and e['content']=='declined' for e in events)
        assert any(e['type']=='skill_result' and e['status']=='denied' for e in events)


def test_plain_chat_api_and_connection_examples(platform):
    with TestClient(create_app(platform,authentication=False),base_url="http://localhost") as client:
        example=client.get('/api/connection-examples').json()['imap_read']
        assert example=={'folder':'INBOX','query':'ALL','limit':5}
        r=client.post('/api/chat',json={'conversation':True,'model':'demo:office','message':'hello'})
        assert r.status_code==200 and '"type": "done"' in r.text
        chats=client.get('/api/conversations').json()
        assert len(chats)==1 and chats[0]['scope']=='conversation'
        history=client.get('/api/conversations/'+chats[0]['id']).json()
        assert history[0]['content']=='hello'
        assert history[1]['_speaker']=='plain_chat'


def test_poll_and_conversation_history_are_isolated(platform):
    import pyotp
    app=create_app(platform)
    with TestClient(app,base_url='http://localhost') as alice,TestClient(app,base_url='http://localhost') as bob:
        alice.post('/api/auth/setup',json={'username':'alice','password':'alice-long-password'})
        def login(client,name,password):
            data=client.post('/api/auth/login',json={'username':name,'password':password}).json()
            assert client.post('/api/auth/verify',json={'challenge':data['challenge'],'code':pyotp.TOTP(data['secret']).now()}).status_code==200
        login(alice,'alice','alice-long-password')
        assert alice.post('/api/users',json={'username':'bobby','password':'bobby-long-password'}).status_code==200
        login(bob,'bobby','bobby-long-password')
        run=alice.post('/api/chat',json={'conversation':True,'model':'demo:office','message':'private-conversation','transport':'poll'}).json()['run_id']
        assert bob.get('/api/chat/runs/'+run).status_code==404
        assert bob.post('/api/chat/runs/'+run+'/cancel',json={}).status_code==404
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            data=alice.get('/api/chat/runs/'+run).json()
            if data['done']:break
            time.sleep(.01)
        conversations=alice.get('/api/conversations').json();assert len(conversations)==1
        id_=conversations[0]['id']
        assert bob.get('/api/conversations').json()==[]
        assert bob.get('/api/conversations/'+id_).json()==[]


def test_plain_chat_exposes_enabled_read_connections_only(platform):
    provider=Recorder()
    platform.config.connections.imap.enabled=True
    platform.config.connections.imap.host='mail.example.com'
    platform.config.connections.imap.username='alice'
    platform.config.connections.jira.enabled=True
    platform.config.connections.ssh.enabled=True
    events=list(platform.runner.run_conversation('Check my mail','demo:office',provider=provider))
    offered={t['function']['name'] for t in provider.calls[-1][1]}
    assert {'imap_read','jira_search','jira_issue'}<=offered
    assert not {'ssh_run','jira_create_issue','jira_update_issue','caldav_read','web_search'}&offered
    session=events[0]['session_id']
    list(platform.runner.run_conversation('Disable sources','demo:office',session_id=session,connections=[],provider=provider))
    assert {t['function']['name'] for t in provider.calls[-1][1]}==CHAT_UTILITIES
    assert platform.runner.conversation_memory().load(session)[-2]['_connections']==[]
    list(platform.runner.run_conversation('Mail only','demo:office',connections=['imap'],provider=provider))
    assert {t['function']['name'] for t in provider.calls[-1][1]}=={'imap_read'}|CHAT_UTILITIES


def test_plain_chat_requires_confirmation_before_mail_read(platform,monkeypatch):
    class MailProvider:
        def chat(self,messages,tools):
            if any(m['role']=='tool' for m in messages):return {'role':'assistant','content':'finished'}
            assert any(t['function']['name']=='imap_read' for t in tools)
            return {'role':'assistant','content':None,'tool_calls':[{'id':'mail','type':'function','function':{'name':'imap_read','arguments':'{"folder":"INBOX","query":"ALL","limit":1}'}}]}
    platform.config.connections.imap.enabled=True
    read=[]
    monkeypatch.setattr(platform.runner.builtins,'run',lambda *a:read.append(a) or {'messages':[]})
    list(platform.runner.run_conversation('read','demo:office',provider=MailProvider(),confirm=lambda _:False))
    assert read==[]
    list(platform.runner.run_conversation('read','demo:office',provider=MailProvider(),confirm=lambda _:True))
    assert read[0][0]=='imap_read'
