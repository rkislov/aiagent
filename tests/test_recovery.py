import base64
from concurrent.futures import ThreadPoolExecutor
import pyotp
import pytest
from superagent.auth import Auth
from fastapi.testclient import TestClient
from superagent.web.app import create_app


def enrolled(tmp_path, username='alice'):
    auth=Auth(tmp_path)
    auth.create(username,'long-private-password')
    challenge=auth.login(username,'long-private-password')
    session,codes=auth.verify(challenge['challenge'],pyotp.TOTP(challenge['secret']).now())
    return auth,challenge,session,codes


def test_qr_and_hashed_codes(tmp_path):
    auth,challenge,session,codes=enrolled(tmp_path)
    assert base64.b64decode(challenge['qr'].split(',')[1]).startswith(b'\x89PNG\r\n\x1a\n')
    assert challenge['uri'].startswith('otpauth://totp/')
    assert len(codes)==len(set(codes))==10
    raw=auth.db.read_bytes()
    assert all(c.encode() not in raw and c.replace('-','').encode() not in raw for c in codes)
    next_login=auth.login('alice','long-private-password')
    assert 'qr' not in next_login and 'secret' not in next_login
    session2,new_codes=auth.verify(next_login['challenge'],codes[0].upper().replace('-',' '))
    assert auth.user(session2)['username']=='alice'
    assert new_codes is None
    attempt=auth.login('alice','long-private-password')
    with pytest.raises(ValueError):auth.verify(attempt['challenge'],codes[0])


def test_atomic_use(tmp_path):
    auth,_,_,codes=enrolled(tmp_path)
    tokens=[auth.login('alice','long-private-password')['challenge'] for _ in range(2)]
    def attempt(token):
        try:auth.verify(token,codes[0]);return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt,tokens))==[False,True]


def test_rotation_and_user_scope(tmp_path):
    auth,_,session,codes=enrolled(tmp_path)
    uid=auth.user(session)['id']
    with pytest.raises(ValueError):auth.rotate_recovery(uid,'incorrect',codes[0])
    new=auth.rotate_recovery(uid,'long-private-password',codes[0])
    assert set(new).isdisjoint(codes)
    attempt=auth.login('alice','long-private-password')
    with pytest.raises(ValueError):auth.verify(attempt['challenge'],codes[1])
    auth.verify(attempt['challenge'],new[0])
    auth.create('bob','long-private-password')
    bob=auth.login('bob','long-private-password')
    with pytest.raises(ValueError):auth.verify(bob['challenge'],new[1])


def test_api_recovery_requires_session_and_returns_codes(platform):
    with TestClient(create_app(platform),base_url='http://localhost') as client:
        assert client.post('/api/auth/recovery',json={}).status_code==401
        client.post('/api/auth/setup',json={'username':'alice','password':'long-private-password'})
        challenge=client.post('/api/auth/login',json={'username':'alice','password':'long-private-password'}).json()
        assert 'no-store' in client.post('/api/auth/login',json={'username':'nobody','password':'wrong'}).headers['cache-control']
        response=client.post('/api/auth/verify',json={'challenge':challenge['challenge'],'code':pyotp.TOTP(challenge['secret']).now()})
        codes=response.json()['recovery_codes']
        assert len(codes)==10
        rotated=client.post('/api/auth/recovery',json={'password':'long-private-password','code':codes[0]})
        assert rotated.status_code==200
        assert len(rotated.json()['recovery_codes'])==10
        assert client.get('/api/auth/state').json()['authenticated']
