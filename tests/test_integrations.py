import json
from unittest.mock import MagicMock
import pytest
import httpx
from superagent.settings import update, public
from superagent.core.agent_loop import AgentRunner
from superagent.integrations.catalog import BUILTINS


def test_settings_secrets(platform):
    draft = public(platform.config)
    draft['providers']['openai']['api_key'] = 'private-key'
    draft['connections']['imap']['password'] = 'private-password'
    saved = update(platform, draft)
    assert 'private-key' not in json.dumps(saved)
    assert 'private-password' not in json.dumps(saved)
    assert saved['providers']['openai']['api_key_present']
    update(platform, saved)
    assert platform.config.providers['openai'].key() == 'private-key'
    assert (platform.root / 'config.yaml').stat().st_mode & 0o077 == 0
    assert platform.audit.redact('private-password') == '[REDACTED]'


def test_disabled_and_immutable(platform):
    with pytest.raises(ValueError, match='выключено'):
        platform.runner.builtins.run('rss_read', {'feed':'', 'limit':2})
    with pytest.raises(ValueError, match='перезаписать'):
        platform.registry.save_skill('rss_read', '', '')


def mock_http(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_rss_atom_and_allowlist(platform, monkeypatch):
    cfg = platform.config.connections.rss
    cfg.enabled = True
    from superagent.gateway.config import RSSFeed
    cfg.feeds = [RSSFeed(name='news', url='https://example.com/feed')]
    mock_http(monkeypatch, lambda r: httpx.Response(200, text='<feed xmlns="http://www.w3.org/2005/Atom"><title>News</title><entry><title>Hello</title><link href="https://example.com/a"/><summary>World</summary></entry></feed>'))
    out = platform.runner.builtins.run('rss_read', {'feed':'news','limit':1})
    assert out['entries'][0]['title'] == 'Hello'
    with pytest.raises(ValueError, match='не настроена'):
        platform.runner.builtins.run('rss_read', {'feed':'unknown','limit':1})


def test_search(platform, monkeypatch):
    cfg=platform.config.connections.search
    cfg.enabled=True
    def handler(request):
        assert json.loads(request.content)['query'] == 'python'
        return httpx.Response(200,json={'results':[{'title':'Python','url':'https://python.org','content':'Language'}]})
    mock_http(monkeypatch,handler)
    assert platform.runner.builtins.run('web_search', {'query':'python','limit':1})['results'][0]['title']=='Python'


def test_imap_readonly(platform,monkeypatch):
    cfg=platform.config.connections.imap
    cfg.enabled=True;cfg.host='example.com';cfg.username='user'
    mailbox=MagicMock()
    mailbox.__enter__.return_value=mailbox
    mailbox.select.return_value=('OK',[])
    mailbox.uid.side_effect=[('OK',[b'12']),('OK',[(b'12',b'Subject: Test\r\nContent-Type: text/plain\r\n\r\nHello')])]
    monkeypatch.setattr('imaplib.IMAP4_SSL',lambda *a,**k:mailbox)
    assert platform.runner.builtins.run('imap_read',{'folder':'INBOX','query':'ALL','limit':1})['messages'][0]['subject']=='Test'
    assert mailbox.select.call_args.kwargs['readonly'] is True
    assert 'BODY.PEEK' in mailbox.uid.call_args.args[2]


def test_skill_model_routes(platform,monkeypatch):
    from test_runner import Scripted,call
    agent=platform.registry.agents['office_assistant']
    agent.skill_models={'report_gen':'openai:specialist'}
    specialist=MagicMock()
    specialist.chat.return_value={'role':'assistant','content':'analysis'}
    monkeypatch.setattr('superagent.core.agent_loop.make_provider',lambda *args:specialist)
    provider=Scripted([call('report_gen',{'classified':[]}),{'role':'assistant','content':'done'}])
    events=list(platform.runner.run('office_assistant','report',provider=provider))
    assert next(e for e in events if e['type']=='skill_result')['output']['analysis_model']=='openai:specialist'
    assert specialist.chat.call_count==1


def test_caldav_read_only(platform, monkeypatch):
    from icalendar import Calendar
    from datetime import datetime, timezone
    cfg=platform.config.connections.caldav
    cfg.enabled=True;cfg.url='https://calendar.example.com';cfg.username='test'
    event=MagicMock()
    event.icalendar_instance=Calendar.from_ical('BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:one\r\nSUMMARY:Meeting\r\nDTSTART:20261121T090000Z\r\nDTEND:20261121T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR')
    calendar=MagicMock();calendar.name='Work';calendar.url='https://calendar.example.com/work';calendar.search.return_value=[event]
    client=MagicMock();client.__enter__.return_value=client;client.principal.return_value.calendars.return_value=[calendar]
    monkeypatch.setattr('caldav.DAVClient',lambda **kwargs:client)
    out=platform.runner.builtins.run('caldav_read',{'start':'2026-11-21T00:00:00+00:00','end':'2026-11-22T00:00:00+00:00','calendar':'Work','limit':10})
    assert out['events'][0]['summary']=='Meeting'
    assert not calendar.save_event.called


def test_channel_confirmation_denied(platform):
    from superagent.channels import reply
    from superagent.gateway.config import ChannelConfig
    cfg=ChannelConfig(type='telegram')
    answer=reply(platform,cfg,'123','сохрани отчёт')
    assert not (platform.root/'data/workspace/report.txt').exists()
    assert any(x['status']=='denied' for x in platform.audit.query())
