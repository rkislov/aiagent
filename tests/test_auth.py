import time
import pyotp
from fastapi.testclient import TestClient
from superagent.web.app import create_app


def login(c, username, password):
    data=c.post('/api/auth/login',json={'username':username,'password':password}).json()
    secret=data['secret']
    response=c.post('/api/auth/verify',json={'challenge':data['challenge'],'code':pyotp.TOTP(secret).now()})
    assert response.status_code==200
    assert 'HttpOnly' in response.headers['set-cookie']
    return secret,data['challenge']


def test_auth_and_full_isolation(platform):
    app=create_app(platform)
    with TestClient(app,base_url='http://localhost') as a, TestClient(app,base_url='http://localhost') as b:
        assert a.get('/api/workspace').status_code==401
        assert a.post('/api/auth/setup',json={'username':'alice','password':'alice-long-password'}).status_code==200
        secret,challenge=login(a,'alice','alice-long-password')
        assert a.post('/api/auth/verify',json={'challenge':challenge,'code':pyotp.TOTP(secret).now()}).status_code==400
        assert a.post('/api/users',json={'username':'bob','password':'bob-long-password'}).status_code==200
        login(b,'bob','bob-long-password')
        agent={'name':'private_agent','model':'demo:office','instructions':'private alice instructions','skills':[]}
        assert a.put('/api/agents/private_agent',json=agent).status_code==200
        assert not any(x['name']=='private_agent' for x in b.get('/api/workspace').json()['agents'])
        assert b.post('/api/users',json={'username':'eve','password':'eve-long-password'}).status_code==403
        settings=a.get('/api/settings').json()
        settings['providers']['openai']['api_key']='alice-secret'
        assert a.put('/api/settings',json=settings).status_code==200
        assert not b.get('/api/settings').json()['providers']['openai']['api_key_present']
        r=a.post('/api/chat',json={'agent':'private_agent','message':'hello','session_id':'private_session'})
        assert 'done' in r.text
        assert b.get('/api/audit').json()==[]
        assert b.get('/api/sessions/private_agent').status_code==400
        assert a.post('/api/auth/logout',json={}).status_code==200
        assert a.get('/api/workspace').status_code==401
