from fastapi.testclient import TestClient
from superagent.web.app import create_app


def test_configured_hostname_https_and_origin(platform):
    platform.config.web_hosts=['localhost','ai.kislovs.ru']
    with TestClient(create_app(platform),base_url='https://ai.kislovs.ru') as client:
        assert client.get('/').status_code==200
        r=client.post('/api/auth/setup',json={'username':'admin','password':'example-long-password'},headers={'Origin':'https://ai.kislovs.ru'})
        assert r.status_code==200
        assert client.get('/api/auth/state',headers={'Host':'evil.example'}).status_code==400
        assert client.post('/api/auth/login',json={},headers={'Origin':'https://evil.example'}).status_code==403
