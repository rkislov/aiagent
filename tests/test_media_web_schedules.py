import base64
import io
import json
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from pydantic import ValidationError
from fastapi.testclient import TestClient
from superagent.web.app import ChatRequest, create_app
from superagent.core.providers.anthropic import anthropic_messages
from superagent.schedules import Schedule, Store, execute_due


def picture():
    data=io.BytesIO();Image.new('RGB',(4,4),'red').save(data,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(data.getvalue()).decode()


def test_images_are_validated_and_bounded():
    image=picture()
    assert ChatRequest(agent='office_assistant',message='look',images=[image]).images==[image]
    for invalid in ['https://127.0.0.1/private', 'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,aGVsbG8=', 'data:image/png;base64,'+'a'*400101]:
        with pytest.raises(ValidationError):ChatRequest(agent='office_assistant',message='look',images=[invalid])
    with pytest.raises(ValidationError):ChatRequest(agent='office_assistant',message='look',images=[image]*5)


def test_multimodal_message_persisted_and_converted(platform):
    from superagent.core.providers.base import ProviderError
    agent=platform.registry.agents['office_assistant'];agent.model='openai:vision'
    provider=Mock();provider.stream.return_value=iter([{'type':'message','message':{'role':'assistant','content':'red'}}])
    events=list(platform.runner.run(agent.name,'what color?',provider=provider,images=[picture()]))
    assert events[-1]['content']=='red'
    history=platform.runner.memory(agent).load(events[0]['session_id'])
    assert history[0]['content'][1]['image_url']['url']==picture()
    converted=anthropic_messages(history)
    assert converted[0]['content'][1]['source']['media_type']=='image/png'
    assert converted[0]['content'][1]['source']['data']==picture().split(',')[1]


def test_web_toggle_controls_tools_on_server(platform):
    agent=platform.registry.agents['office_assistant'];agent.skills.append('web_search')
    offered=[]
    class Provider:
        def chat(self,messages,tools):
            offered.append({t['function']['name'] for t in tools})
            return {'role':'assistant','content':'ok'}
    list(platform.runner.run(agent.name,'hello',provider=Provider(),web_enabled=False))
    assert not {'web_read','web_search'}&offered[-1]
    list(platform.runner.run(agent.name,'hello',provider=Provider(),web_enabled=True))
    assert 'web_read' in offered[-1] and 'web_search' in offered[-1] # agent explicitly includes search


def test_web_reader_rejects_private_dns(monkeypatch):
    from superagent.integrations.web_read import public_address,read_page
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**kw:[(2,1,6,'',('127.0.0.1',80))])
    with pytest.raises(ValueError,match='публичных'):public_address('localhost',80)
    for url in ['file:///etc/passwd','http://user:password@example.com','https://example.com:8080/']:
        with pytest.raises(ValueError):read_page(url)


def test_tenant_settings_normalize_environment_fields(platform, monkeypatch):
    from superagent.settings import update,public
    from superagent.gateway.config import credential_entries
    (platform.root/'.tenant').touch()
    monkeypatch.setenv('OPENAI_API_KEY','server-secret')
    draft=public(platform.config);draft['connections']['imap']['password_env']='mistaken-input'
    result=update(platform,draft)
    assert result['connections']['imap']['password_env'] is None
    assert platform.config.providers['openai'].key()==''
    assert all(not getattr(e,f,None) for e in credential_entries(platform.config) for f in ['api_key_env','password_env','secret_env'])


def test_schedule_times_persistence_claim_and_recovery(platform):
    now=datetime(2026,11,22,6,0,tzinfo=timezone.utc).timestamp()
    daily=Schedule(name='daily',agent='office_assistant',message='hello',local_time='09:01')
    assert daily.next_time(now)==now+60
    weekly=daily.model_copy(update={'mode':'weekly','weekday':0})
    assert datetime.fromtimestamp(weekly.next_time(now),timezone.utc).weekday()==0
    store=Store(platform.root)
    draft=daily.model_copy(update={'mode':'interval','interval_minutes':1})
    id_=store.save(draft)
    with store.connect() as db:db.execute('UPDATE jobs SET next_run=? WHERE id=?',(now,id_))
    claim=store.claim(time.time());assert claim[0]==id_
    assert Store(platform.root).claim(time.time()+3600) is None
    store.recover();assert store.list()[0]['status']=='interrupted'
    store.delete(id_);assert not store.list()


def test_scheduled_protected_actions_are_never_approved(platform, monkeypatch):
    draft=Schedule(name='test',agent='office_assistant',message='hello',mode='interval',allow_reads=True)
    store=Store(platform.root);id_=store.save(draft)
    with store.connect() as db:db.execute('UPDATE jobs SET next_run=0 WHERE id=?',(id_,))
    def run(*args,confirm,**kwargs):
        assert confirm({'skill':'rss_read'})
        assert not confirm({'skill':'ssh_run'})
        assert not confirm({'skill':'jira_create_issue'})
        assert not confirm({'skill':'save_report'})
        yield {'type':'skill_result','status':'denied'}
        yield {'type':'done','content':'protected'}
    monkeypatch.setattr(platform.runner,'run',run)
    execute_due(platform,threading.Event())
    assert store.list()[0]['status']=='needs_confirmation'


def test_schedules_api_requires_auth_and_scopes_to_user(platform):
    from superagent.auth import Auth
    from superagent.service import Platform
    auth=Auth(platform.root);auth.create('alice','alice-password-long',bootstrap=True);auth.create('bobby','bobby-password-long')
    with TestClient(create_app(platform)) as c:assert c.get('/api/schedules').status_code==401
    services=[]
    for uid, in auth.connect().execute('SELECT id FROM users'):
        services.append(auth.service({'id':uid}))
    Store(services[0].root).save(Schedule(name='private',agent='office_assistant',message='private'))
    assert Store(services[1].root).list()==[]
    for service in services:service.close()
