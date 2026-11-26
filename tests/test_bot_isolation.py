import json
from pydantic import SecretStr
import pytest
from superagent.channel_context import user_service, Dialogs, identity_key
from superagent.channels import mattermost_reply
from superagent.gateway.config import ChannelConfig


def cfg():
    return ChannelConfig(type='mattermost',url='https://mail.example.com',agent='',model='demo:office',
                         owner_user_id='alice',allow_direct_messages=True)


def post(user,text):return {'user_id':user,'channel_id':'dm-'+user,'message':text}


def test_remote_users_cannot_read_owner_or_other_user_data(platform,monkeypatch):
    platform.config.connections.imap.enabled=True
    platform.config.connections.imap.password=SecretStr('owner-secret')
    platform.config.connections.ssh.enabled=True
    monkeypatch.setenv('IMAP_PASSWORD','environment-secret')
    memory=platform.runner.conversation_memory();memory.ensure('owner-dialog','__conversation__','private-owner-title')
    memory.append('owner-dialog',{'role':'user','content':'private-owner-message'})
    c=cfg();bob=user_service(platform,'bot',c,'bob');carol=user_service(platform,'bot',c,'carol')
    assert user_service(platform,'bot',c,'alice') is platform
    assert bob is user_service(platform,'bot',c,'bob') and bob.root!=carol.root!=platform.root
    assert not bob.config.channels and not bob.config.connections.imap.enabled and not bob.config.connections.ssh.enabled
    assert not bob.config.connections.imap.key()
    assert not bob.registry.agents
    assert not list((bob.root/"skills").iterdir())
    assert bob.runner.conversation_memory().load('owner-dialog')==[]
    assert 'private-owner-title' not in mattermost_reply(platform,'bot',c,post('bob','/ai chats'))
    assert 'не найден' in mattermost_reply(platform,'bot',c,post('bob','/ai use owner-dialog'))
    assert bob.runner.builtins.root==bob.root
    bob.runner.conversation_memory().ensure('bob-dialog','__conversation__','bob-only')
    bob.runner.conversation_memory().append('bob-dialog',{'role':'user','content':'bob-only-text'})
    result=carol.runner.builtins.run('dialog_search',{'query':'bob-only-text','limit':10})
    assert not result['dialogs']


def test_bot_dialog_commands_and_web_state_are_per_user_and_dialog(platform):
    c=cfg()
    mattermost_reply(platform,'bot',c,post('bob','/ai help'))
    service=user_service(platform,'bot',c,'bob');dialogs=Dialogs(service,identity_key('bot',c,'bob'))
    first=dialogs.current()
    assert 'включён' in mattermost_reply(platform,'bot',c,post('bob','/ai web on'))
    mattermost_reply(platform,'bot',c,post('bob','/ai new'));second=dialogs.current()
    assert second!=first
    assert 'выключен' in mattermost_reply(platform,'bot',c,post('bob','/ai web status'))
    # First dialog gets a title on its first actual turn.
    service.runner.conversation_memory().ensure(first,'__conversation__','first')
    assert 'включён' in mattermost_reply(platform,'bot',c,post('bob','/ai use '+first))
    assert 'выключен' in mattermost_reply(platform,'bot',c,post('carol','/ai web status'))
    assert Dialogs(service,identity_key('bot',c,'bob')).current()==first


def test_web_status_overrides_old_history_and_restores_without_argument(platform):
    class Provider:
        def chat(self,messages,tools):
            self.messages,self.tools=json.loads(json.dumps(messages)),tools
            return {'role':'assistant','content':'Веб выключен'}
    p=Provider();events=list(platform.runner.run_conversation('hello','demo:office',provider=p))
    session=events[0]['session_id']
    list(platform.runner.run_conversation('search now','demo:office',session_id=session,web_enabled=True,provider=p))
    assert 'web_read' in {t['function']['name'] for t in p.tools}
    assert p.messages[-2]['role']=='system' and 'ВКЛЮЧЁН' in p.messages[-2]['content']
    list(platform.runner.run_conversation('continue','demo:office',session_id=session,provider=p))
    assert 'web_read' in {t['function']['name'] for t in p.tools}
    assert platform.runner.conversation_memory().load(session)[-2]['_web_enabled'] is True
    list(platform.runner.run_conversation('off','demo:office',session_id=session,web_enabled=False,provider=p))
    assert 'web_read' not in {t['function']['name'] for t in p.tools}


def test_web_settings_survive_history_reload_and_cannot_create_foreign_dialog(platform):
    from fastapi.testclient import TestClient
    from superagent.web.app import create_app
    with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as client:
        client.post('/api/chat',json={'conversation':True,'model':'demo:office','message':'test'})
        session=client.get('/api/conversations').json()[0]['id']
        assert client.put('/api/conversations/'+session+'/settings',json={'web_enabled':True}).json()=={'web_enabled':True,'knowledge_collections':[]}
        assert client.get('/api/conversations/'+session+'/settings').json()=={'web_enabled':True,'knowledge_collections':[]}
        assert client.put('/api/conversations/foreign/settings',json={'web_enabled':True}).status_code==404
        from superagent.core.memory import Memory
        assert Memory(platform.root/'data/conversations.db').web(session)
