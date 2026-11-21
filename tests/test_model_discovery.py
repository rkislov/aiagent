import httpx
import pytest
from fastapi.testclient import TestClient
from superagent.web.app import create_app
from superagent.core.providers.discovery import list_models
from superagent.gateway.config import ProviderConfig
from test_providers import transport


@pytest.mark.parametrize('base',['https://api.openai.com/v1','https://api.deepseek.com','https://api.deepseek.com/v1'])
def test_official_discovery(monkeypatch,base):
    def handler(request):
        assert request.method=='GET'
        assert str(request.url)==base+'/models'
        assert request.headers['authorization']=='Bearer private-test-key'
        return httpx.Response(200,json={'data':[{'id':'model-b'},{'id':'model-a'},{'id':'model-a'},{'id':12}]})
    transport(monkeypatch,handler)
    assert list_models(base,'private-test-key')==['model-a','model-b']


@pytest.mark.parametrize('status,body',[(401,{'error':'private-test-key'}),(429,{'error':'rate limit'}),(200,{'data':[]}),(200,{'invalid':True})])
def test_failed_discovery_keeps_settings(platform,monkeypatch,status,body):
    transport(monkeypatch,lambda r:httpx.Response(status,json=body))
    old=platform.config.providers['openai'].models[:]
    with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as client:
        response=client.post('/api/providers/models',json={'name':'openai','base_url':'https://api.openai.com/v1','api_key':'private-test-key'})
    assert response.status_code==400
    assert 'private-test-key' not in response.text
    assert platform.config.providers['openai'].models==old
    assert not (platform.root/'config.yaml').exists()


def test_unsaved_and_stored_key_scope(platform,monkeypatch):
    platform.config.providers['openai']=ProviderConfig(api_key_env=None,api_key='stored-key',models=['existing'])
    seen=[]
    def handler(request):
        seen.append(request.headers['authorization'])
        return httpx.Response(200,json={'data':[{'id':'model-a'}]})
    transport(monkeypatch,handler)
    with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as client:
        payload={'name':'openai','base_url':'https://api.openai.com/v1'}
        assert client.post('/api/providers/models',json=payload).json()['models']==['model-a']
        assert client.post('/api/providers/models',json={**payload,'api_key':'new-key'}).status_code==200
        assert client.post('/api/providers/models',json={**payload,'base_url':'https://api.deepseek.com'}).status_code==400
        assert client.post('/api/providers/models',json={**payload,'base_url':'https://evil.example','api_key':'new-key'}).status_code==400
    assert seen==['Bearer stored-key','Bearer new-key']
    assert platform.config.providers['openai'].key()=='stored-key'
    assert platform.config.providers['openai'].models==['existing']


def test_requires_login(platform):
    with TestClient(create_app(platform),base_url='http://localhost') as client:
        assert client.post('/api/providers/models',json={}).status_code==401


def test_no_redirect_or_remote_body_leak(monkeypatch):
    transport(monkeypatch,lambda r:httpx.Response(302,headers={'Location':'https://evil.example'},text='sensitive body'))
    with pytest.raises(ValueError) as error:list_models('https://api.openai.com/v1','key')
    assert 'sensitive' not in str(error.value)


def test_save_only_list_preserves_settings_and_key(platform,monkeypatch):
    from superagent.gateway.config import load_config
    platform.config.providers['openai']=ProviderConfig(api_key_env=None,api_key='stored-key',models=['old'])
    platform.config.context_turns=17
    transport(monkeypatch,lambda r:httpx.Response(200,json={'data':[{'id':'new-model'}]}))
    with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as client:
        payload={'name':'openai','base_url':'https://api.openai.com/v1','api_key':'unsaved-key'}
        discovery=client.post('/api/providers/models',json=payload).json()
        response=client.post('/api/providers/models/save',json={'ticket':discovery['save_ticket']})
        assert response.json()=={'saved':True,'models':['new-model']}
        assert 'stored-key' not in response.text and 'unsaved-key' not in response.text
        assert client.get('/api/workspace').json()['models'].count('openai:new-model')==1
        assert client.post('/api/providers/models/save',json={'ticket':discovery['save_ticket']}).status_code==400
    saved=load_config(platform.root)
    assert saved.providers['openai'].models==['new-model']
    assert saved.providers['openai'].key()=='stored-key'
    assert saved.context_turns==17


def test_rejects_stale_or_changed_credentials(platform,monkeypatch):
    platform.config.providers['openai']=ProviderConfig(api_key_env=None,api_key='first-key',models=['old'])
    transport(monkeypatch,lambda r:httpx.Response(200,json={'data':[{'id':'new-model'}]}))
    with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as client:
        payload={'name':'openai','base_url':'https://api.openai.com/v1'}
        first=client.post('/api/providers/models',json=payload).json()['save_ticket']
        second=client.post('/api/providers/models',json=payload).json()['save_ticket']
        assert client.post('/api/providers/models/save',json={'ticket':first}).status_code==400
        from pydantic import SecretStr
        platform.config.providers['openai'].api_key=SecretStr('second-key')
        assert client.post('/api/providers/models/save',json={'ticket':second}).status_code==400
    assert platform.config.providers['openai'].models==['old']


def test_discovery_save_creates_only_minimal_provider(platform,monkeypatch):
    from superagent.gateway.config import load_config
    transport(monkeypatch,lambda r:httpx.Response(200,json={'data':[{'id':'new-model'}]}))
    with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as client:
        data=client.post('/api/providers/models',json={'name':'new_service','base_url':'https://api.deepseek.com','api_key':'not-saved'}).json()
        assert client.post('/api/providers/models/save',json={'ticket':data['save_ticket']}).status_code==200
    provider=load_config(platform.root).providers['new_service']
    assert provider.models==['new-model']
    assert provider.api_key is None and provider.api_key_env is None
