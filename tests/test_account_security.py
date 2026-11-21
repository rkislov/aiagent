import pytest
import pyotp
from superagent.auth import Auth


def enroll(auth,name):
    auth.create(name,'old-password-long')
    c=auth.login(name,'old-password-long')
    token,codes=auth.verify(c['challenge'],pyotp.TOTP(c['secret']).now())
    return token,codes,c['secret']


def test_password_change_invalidates_all_sessions(tmp_path):
    auth=Auth(tmp_path);token,codes,secret=enroll(auth,'alice')
    uid=auth.user(token)['id']
    with pytest.raises(ValueError):auth.change_password(uid,'wrong',codes[0],'new-password-long')
    assert auth.user(token)
    pending=auth.login('alice','old-password-long')['challenge']
    auth.change_password(uid,'old-password-long',codes[0],'new-password-long')
    assert auth.user(token) is None
    with pytest.raises(ValueError):auth.verify(pending,codes[1])
    with pytest.raises(ValueError):auth.login('alice','old-password-long')
    challenge=auth.login('alice','new-password-long')
    assert not challenge['enroll']
    assert auth.verify(challenge['challenge'],codes[1])[0]


def test_admin_reset_revokes_target_and_reenrolls(tmp_path):
    auth=Auth(tmp_path);admin,acodes,_=enroll(auth,'admin');user,ucodes,secret=enroll(auth,'alice')
    aid=auth.user(admin)['id'];uid=auth.user(user)['id']
    with pytest.raises(PermissionError):auth.reset_two_factor(uid,'admin','old-password-long',ucodes[0])
    with pytest.raises(ValueError):auth.reset_two_factor(aid,'admin','old-password-long',acodes[0])
    auth.reset_two_factor(aid,'alice','old-password-long',acodes[0])
    assert auth.user(user) is None
    assert auth.user(admin)
    c=auth.login('alice','old-password-long')
    assert c['enroll'] and c['secret']!=secret
    with pytest.raises(ValueError):auth.verify(c['challenge'],ucodes[1])
    assert auth.verify(c['challenge'],pyotp.TOTP(c['secret']).now())[1]
    with auth.connect() as db:
        assert db.execute('SELECT action FROM security_events').fetchone()[0]=='two_factor_reset'


def test_security_endpoints(platform):
    from fastapi.testclient import TestClient
    from superagent.web.app import create_app
    app=create_app(platform)
    with TestClient(app,base_url='http://localhost') as admin, TestClient(app,base_url='http://localhost') as user:
        admin.post('/api/auth/setup',json={'username':'admin','password':'old-password-long'})
        def login(client,name):
            c=client.post('/api/auth/login',json={'username':name,'password':'old-password-long'}).json()
            return client.post('/api/auth/verify',json={'challenge':c['challenge'],'code':pyotp.TOTP(c['secret']).now()}).json()['recovery_codes']
        acodes=login(admin,'admin')
        admin.post('/api/users',json={'username':'alice','password':'old-password-long'})
        ucodes=login(user,'alice')
        assert user.post('/api/users/reset-2fa',json={'username':'admin'}).status_code==403
        assert admin.post('/api/users/reset-2fa',json={'username':'alice','password':'old-password-long','code':acodes[0]}).status_code==200
        assert user.get('/api/workspace').status_code==401
        assert admin.post('/api/auth/password',json={'password':'old-password-long','code':acodes[1],'new_password':'new-password-long'}).status_code==200
        assert admin.get('/api/workspace').status_code==401
