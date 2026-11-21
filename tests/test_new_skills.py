import httpx
import pytest
from test_integrations import mock_http


def test_pure_skills(platform):
    hub=platform.runner.builtins
    assert hub.run('commitment_scan',{'text':'Пришлю отчёт до 2026-12-01.'})['commitments'][0]['explicit_dates']==['2026-12-01']
    assert hub.run('reply_drafts',{'messages':[{'subject':'Вопрос'}]})['drafts'][0]['sent'] is False
    invoices=[{'vendor':'Acme','number':'1','amount':'100'},{'vendor':'Acme','number':'1','amount':'-2'}]
    issues=hub.run('invoice_check',{'invoices':invoices})['issues']
    assert {i['issue'] for i in issues}=={'possible_duplicate','invalid_amount'}


def test_odata_only_get_allowed_entity(platform,monkeypatch):
    c=platform.config.connections.one_c;c.enabled=True;c.url='https://example.com/odata/standard.odata';c.entities=['Document_Test'];c.username='user'
    def request(r):
        assert r.method=='GET'
        assert r.url.params['$top']=='5'
        return httpx.Response(200,json={'value':[{'Ref_Key':'1'}]})
    mock_http(monkeypatch,request)
    p={'entity':'Document_Test','filter':'','select':'','limit':5}
    assert platform.runner.builtins.run('one_c_read',p)['records']==[{'Ref_Key':'1'}]
    with pytest.raises(ValueError):platform.runner.builtins.run('one_c_read',{**p,'entity':'Other'})


def test_ssh_must_be_allowed_and_pinned(platform):
    c=platform.config.connections.ssh;c.enabled=True
    with pytest.raises(ValueError):platform.runner.builtins.run('ssh_run',{'command':'rm -rf /'})
    with pytest.raises(ValueError):platform.runner.builtins.run('ssh_run',{'command':'uptime'})


def test_composite_needs_sources(platform):
    with pytest.raises(ValueError):platform.runner.builtins.run('morning_digest',{'start':'2026-11-21T00:00:00Z','end':'2026-11-22T00:00:00Z','limit':5})


def test_ssh_success_is_audited_after_confirmation(platform,monkeypatch):
    from unittest.mock import MagicMock
    from test_runner import Scripted,call
    import base64,hashlib
    key=MagicMock();key.asbytes.return_value=b'host-key'
    c=platform.config.connections.ssh;c.enabled=True;c.host='server';c.username='user'
    c.host_key_sha256='SHA256:'+base64.b64encode(hashlib.sha256(b'host-key').digest()).decode().rstrip('=')
    channel=MagicMock();channel.recv_ready.return_value=False;channel.recv_stderr_ready.return_value=False;channel.exit_status_ready.return_value=True;channel.recv_exit_status.return_value=0
    client=MagicMock();client.__enter__.return_value=client;client.get_transport.return_value.open_session.return_value=channel
    def connect(*a,**k):client.set_missing_host_key_policy.call_args.args[0].missing_host_key(client,'server',key)
    client.connect.side_effect=connect
    monkeypatch.setattr('paramiko.SSHClient',lambda:client)
    platform.registry.agents['office_assistant'].skills=['ssh_run']
    platform.registry.agents['office_assistant'].confirmation_required=[]
    events=list(platform.runner.run('office_assistant','uptime',confirm=lambda a:True,provider=Scripted([call('ssh_run',{'command':'uptime'}),{'role':'assistant','content':'done'}])))
    assert any(e['type']=='confirmation' for e in events)
    assert next(e for e in events if e['type']=='skill_result')['status']=='ok'
    channel.exec_command.assert_called_once_with('uptime')
    assert platform.audit.query(skill='ssh_run')[0]['status']=='ok'
