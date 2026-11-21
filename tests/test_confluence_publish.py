import json
import pytest
import httpx
from test_work_tools import connection
from test_integrations import mock_http
from test_runner import Scripted,call
from fastapi.testclient import TestClient
from superagent.web.app import create_app


def payload(edition,update=False):
    return {'page_id':'12' if update else '', 'space_id':'7' if edition=='cloud' and not update else '', 'space_key':'TEAM' if edition=='server' and not update else '', 'parent_id':'', 'title':'Release 1.2', 'markdown':'# Release\n\n- [APP-1](https://example.com/APP-1)\n\n<script>alert(1)</script>'}


def setup(platform,monkeypatch,edition,*,changed=False,conflict=False):
    connection(platform,'confluence',edition=edition)
    reads=[];writes=[]
    def handler(r):
        if r.method=='GET':
            reads.append(r)
            version=4 if changed and len(reads)>1 else 3
            return httpx.Response(200,json={'id':'12','status':'current','title':'Old','version':{'number':version},'spaceId':'7','space':{'key':'TEAM'},'body':{'storage':{'value':'<p>Old</p>'}}})
        writes.append(r)
        if conflict:return httpx.Response(409,json={'error':'conflict'})
        body=json.loads(r.content)
        storage=body['body'] if edition=='cloud' else body['body']['storage']
        assert storage['representation']=='storage' and '<script>' not in storage['value']
        assert '<a href="https://example.com/APP-1">' in storage['value']
        assert r.method in ('POST','PUT')
        if r.method=='PUT':assert body['version']['number']==4
        return httpx.Response(200 if r.method=='PUT' else 201,json={'id':'12' if r.method=='PUT' else '13'})
    mock_http(monkeypatch,handler)
    return reads,writes


@pytest.mark.parametrize('edition',['cloud','server'])
@pytest.mark.parametrize('update',[False,True])
@pytest.mark.parametrize('approved',[False,True])
def test_publication_requires_approval(platform,monkeypatch,edition,update,approved):
    reads,writes=setup(platform,monkeypatch,edition)
    p=payload(edition,update)
    agent=platform.registry.agents['office_assistant'];agent.skills=['confluence_publish_release'];agent.confirmation_required=[]
    def confirm(approval):
        assert not writes
        assert approval['preview']['markdown']==p['markdown']
        assert approval['preview']['title']==p['title']
        return approved
    events=list(platform.runner.run(agent.name,'Publish',confirm=confirm,provider=Scripted([call('confluence_publish_release',p),{'role':'assistant','content':'done'}])))
    assert len(writes)==int(approved)
    assert next(e for e in events if e['type']=='skill_result')['status']==('ok' if approved else 'denied')
    if writes:
        assert writes[0].url.path.startswith('/wiki/api/v2/pages' if edition=='cloud' else '/rest/api/content')
        assert writes[0].method==('PUT' if update else 'POST')


@pytest.mark.parametrize('changed,conflict',[(True,False),(False,True)])
def test_version_conflict_not_overwritten(platform,monkeypatch,changed,conflict):
    reads,writes=setup(platform,monkeypatch,'cloud',changed=changed,conflict=conflict)
    plan=platform.runner.builtins.prepare_write('confluence_publish_release',payload('cloud',True))
    with pytest.raises(ValueError):plan.execute(confirmed=True)
    assert len(writes)==int(conflict)
    with pytest.raises(PermissionError):plan.execute(confirmed=True)


def test_no_bypass_and_single_use(platform,monkeypatch):
    reads,writes=setup(platform,monkeypatch,'cloud')
    p=payload('cloud')
    with pytest.raises(PermissionError):platform.runner.builtins.run('confluence_publish_release',p)
    plan=platform.runner.builtins.prepare_write('confluence_publish_release',p)
    with pytest.raises(PermissionError):plan.execute()
    plan.preview['markdown']='tampered'
    plan.execute(confirmed=True)
    with pytest.raises(PermissionError):plan.execute(confirmed=True)
    with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as c:
        assert c.post('/api/connections/confluence_publish_release/test',json=p).status_code==400
    assert len(writes)==1


def test_invalid_space_and_parent_cannot_write(platform,monkeypatch):
    reads,writes=setup(platform,monkeypatch,'cloud')
    p=payload('cloud')
    with pytest.raises(ValueError):platform.runner.builtins.prepare_write('confluence_publish_release',{**p,'space_id':''})
    with pytest.raises(ValueError):platform.runner.builtins.prepare_write('confluence_publish_release',{**p,'parent_id':'12','space_id':'99'})
    assert not writes
