import json
import subprocess
import sys
from pathlib import Path
import pyotp
import pytest
from superagent.auth import Auth
from superagent.cli import main
from superagent.cli_admin import local_auth


def enrolled(root):
    auth=Auth(root);auth.create('admin','old-password-long')
    challenge=auth.login('admin','old-password-long')
    token,codes=auth.verify(challenge['challenge'],pyotp.TOTP(challenge['secret']).now())
    return auth,token,codes,challenge['secret']


@pytest.mark.parametrize('reset_2fa',[False,True])
def test_cli_admin_reset(platform,monkeypatch,capsys,reset_2fa):
    auth,token,codes,old_secret=enrolled(platform.root)
    content=platform.root/'data/workspace/keep.txt';content.write_text('preserved')
    argv=['superagent','--root',str(platform.root),'auth','reset-admin','admin']+(['--reset-2fa'] if reset_2fa else [])
    monkeypatch.setattr(sys,'argv',argv)
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr('builtins.input',lambda _: 'admin')
    monkeypatch.setattr('getpass.getpass',lambda _: 'new-password-long')
    main()
    output=capsys.readouterr().out
    assert 'new-password-long' not in output
    assert auth.user(token) is None
    with pytest.raises(ValueError):auth.login('admin','old-password-long')
    c=auth.login('admin','new-password-long')
    assert c['enroll']==reset_2fa
    if reset_2fa:
        assert c['secret']!=old_secret
        with pytest.raises(ValueError):auth.verify(c['challenge'],codes[0])
    else:
        assert auth.verify(c['challenge'],codes[0])[0]
    assert content.read_text()=='preserved'
    with auth.connect() as db:
        rows=db.execute('SELECT action FROM security_events').fetchall()
    assert ('cli_admin_password_reset',) in rows


def test_cli_no_tty_no_reset(platform,monkeypatch,capsys):
    auth,token,_,_=enrolled(platform.root)
    monkeypatch.setattr(sys,'argv',['superagent','--root',str(platform.root),'auth','reset-admin','admin'])
    monkeypatch.setattr(sys.stdin,'isatty',lambda:False)
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==1
    assert auth.user(token)


def test_cli_refuses_insecure_db(platform):
    auth,_,_,_=enrolled(platform.root)
    auth.db.chmod(0o666)
    with pytest.raises(ValueError,match='владения'):local_auth(platform.root)
    auth.db.chmod(0o600)


def command(root,*args):
    return subprocess.run([sys.executable,'-m','superagent','--root',str(root),*args],capture_output=True,text=True,cwd=Path(__file__).parents[1],timeout=15)


def test_cli_inspection_and_user_scope(platform):
    auth,_,_,_=enrolled(platform.root)
    auth.create('alice','alice-password-long')
    auth.create('bob','bob-password-long')
    for args in [('status',),('agents','list'),('agents','show','office_assistant'),('models','list'),('config','show'),('channels','list'),('audit',),('sessions','list','office_assistant'),('skills','show','web_search')]:
        result=command(platform.root,*args)
        assert result.returncode==0,result.stdout+result.stderr
    users=command(platform.root,'users','list')
    assert users.returncode==0 and 'admin' in users.stdout and 'alice' in users.stdout
    result=command(platform.root,'--user','alice','status')
    assert result.returncode==0,result.stdout
    path=json.loads(result.stdout)['root']
    assert '/data/users/' in path
    assert command(platform.root,'--user','bob','agents','show','office_assistant').returncode==1
    assert command(platform.root,'--user','missing','status').returncode==1
    assert command(platform.root,'auth','reset-admin','admin','--password','should-not-be-accepted').returncode==2


def test_non_admin_target_not_reset(platform):
    auth,token,_,_=enrolled(platform.root);auth.create('alice','alice-password-long')
    with pytest.raises(ValueError):auth.local_reset('alice','new-password-long',admin_only=True)
    assert auth.user(token)


def test_running_request_detects_reset_in_separate_auth_instance(platform):
    from superagent.auth import RevocableSessionEvent
    auth,token,_,_=enrolled(platform.root)
    cancelled=RevocableSessionEvent(auth,token)
    assert not cancelled.is_set()
    other=Auth(platform.root)
    other.local_reset('admin','new-password-long',admin_only=True)
    assert cancelled.is_set()


def test_cancel_or_password_mismatch_does_not_modify_user(platform,monkeypatch):
    auth,token,_,_=enrolled(platform.root)
    monkeypatch.setattr(sys,'argv',['superagent','--root',str(platform.root),'auth','reset-admin','admin'])
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr('builtins.input',lambda _: 'wrong-user')
    with pytest.raises(SystemExit):main()
    assert auth.user(token)
    monkeypatch.setattr('builtins.input',lambda _: 'admin')
    answers=iter(['new-password-long','different-password'])
    monkeypatch.setattr('getpass.getpass',lambda _:next(answers))
    with pytest.raises(SystemExit):main()
    assert auth.user(token)
