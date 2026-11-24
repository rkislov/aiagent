import json
import threading
import time
from unittest.mock import Mock
import httpx
import pytest
from superagent.channels import mattermost_confirmation,run,reply,validate_channel
from superagent.gateway.config import ChannelConfig
from superagent.channel_runtime import ChannelSupervisor
from superagent.settings import public,update


def config(**kwargs):
    return ChannelConfig(type='mattermost',url='mail.example.com',agent='',model='demo:office',enabled=True,secret='bot-secret',allowed=['room'],**kwargs)


def test_normalized_address_and_invalid_enabled_settings(platform):
    cfg=config();assert cfg.url=='https://mail.example.com'
    validate_channel(platform,cfg)
    draft=public(platform.config)
    draft['channels']['bot']={**cfg.model_dump(),'secret':'bot-secret','allowed':[]}
    with pytest.raises(ValueError,match='разрешённые'):update(platform,draft)
    assert not platform.config.channels


def test_mattermost_confirmation_is_bound_to_sender_and_readonly(platform):
    cfg=config();seen=set();stop=threading.Event();calls=[]
    def handler(request):
        calls.append(request)
        if request.method=='POST':return httpx.Response(201,json={'id':'prompt','create_at':100})
        return httpx.Response(200,json={'posts':{
            'wrong':{'id':'wrong','user_id':'someone-else','create_at':102,'message':'РАЗРЕШАЮ abcdef123456'},
            'right':{'id':'right','user_id':'owner','create_at':103,'message':'РАЗРЕШАЮ abcdef123456'},
        }})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        check=mattermost_confirmation(c,'https://mail.example.com/api/v4',cfg,{'id':'message','channel_id':'room','user_id':'owner'},seen,stop,1)
        assert check({'id':'abcdef1234569999','skill':'imap_read','input':{'folder':'INBOX'}})
        assert 'right' in seen and 'wrong' not in seen
        count=len(calls)
        for skill in ['ssh_run','schedule_save','schedule_control','jira_create_issue']:
            assert not check({'id':'abcdef1234569999','skill':skill,'input':{}})
        assert len(calls)==count


def test_mattermost_uses_plain_chat_and_does_not_reply_to_self(platform,monkeypatch):
    cfg=config();platform.config.channels['bot']=cfg;stop=threading.Event();requests=[]
    def handler(r):
        requests.append(r)
        if r.url.path.endswith('/users/me'):return httpx.Response(200,json={'id':'self'})
        if r.url.path.endswith('/users/me/teams'):return httpx.Response(200,json=[])
        if r.method=='GET':return httpx.Response(200,json={'posts':{
            'a':{'id':'a','user_id':'self','create_at':100,'message':'ignore'},
            'b':{'id':'b','user_id':'owner','create_at':101,'message':'hello'},
        }})
        stop.set();return httpx.Response(201,json={'id':'reply'})
    client=httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr('superagent.channels.httpx.Client',lambda **kw:client)
    seen=[]
    def fake_reply(service,cfg,sender,text,**kwargs):seen.append((sender,text));return 'hello back'
    monkeypatch.setattr('superagent.channels.reply',fake_reply)
    run(platform,'bot',stop=stop)
    assert len(seen)==1 and seen[0][1]=='hello'
    sent=[r for r in requests if r.method=='POST']
    assert len(sent)==1 and json.loads(sent[0].content)['root_id']=='b'


def test_supervisor_starts_and_stops_enabled_channel(platform,monkeypatch):
    platform.config.channels['bot']=config()
    started=threading.Event();stopped=threading.Event()
    def worker(service,name,*,stop,config,on_state):
        on_state('running');started.set();stop.wait(2);stopped.set()
    monkeypatch.setattr('superagent.channel_runtime.run',worker)
    manager=ChannelSupervisor(platform,None)
    try:
        manager.reconcile([platform]);assert started.wait(1)
        assert manager.status(platform)[0]['status']=='running'
        platform.config.channels['bot'].enabled=False
        manager.reconcile([platform]);assert stopped.wait(1)
    finally:
        for entry in manager.workers.values():entry['stop'].set()


def test_channel_reply_uses_shared_plain_chat(platform,monkeypatch):
    def fake(*args,**kwargs):
        assert args[0]=='hello' and args[1]=='demo:office'
        yield {'type':'done','content':'works'}
    monkeypatch.setattr(platform.runner,'run_conversation',fake)
    assert reply(platform,config(),'sender','hello')=='works'
