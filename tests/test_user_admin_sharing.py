import pytest
import pyotp
from fastapi.testclient import TestClient
from superagent.auth import Auth
from superagent.web.app import create_app
from superagent.gateway.models import Agent
from superagent.shared_agents import SharedAgents


def enroll(auth,name):
 auth.create(name,'very-long-password')
 challenge=auth.login(name,'very-long-password')
 token,codes=auth.verify(challenge['challenge'],pyotp.TOTP(challenge['secret']).now())
 return auth.user(token),token,codes


def test_admin_can_manage_user_without_exposing_content(platform):
 auth=Auth(platform.root)
 admin,admin_token,codes=enroll(auth,'admin')
 user,user_token,_=enroll(auth,'alice')
 rows=auth.list_users(admin['id'])
 assert {r['username'] for r in rows}=={'admin','alice'}
 assert all(not ({'hash','totp','password','secret'} & set(r)) for r in rows)
 with pytest.raises(PermissionError):auth.list_users(user['id'])
 victim=auth.service(user);(victim.root/'private.txt').write_text('private-data')
 auth.manage_user(admin['id'],'alice','block','very-long-password',codes[0])
 assert auth.user(user_token) is None
 with pytest.raises(ValueError):auth.login('alice','very-long-password')
 assert (victim.root/'private.txt').read_text()=='private-data'
 auth.manage_user(admin['id'],'alice','unblock','very-long-password',codes[1])
 assert auth.login('alice','very-long-password')['enroll'] is False
 auth.manage_user(admin['id'],'alice','promote','very-long-password',codes[2])
 assert next(r for r in auth.list_users(admin['id']) if r['username']=='alice')['admin']
 auth.manage_user(admin['id'],'alice','reset_password','very-long-password',codes[3],new_password='different-private-password')
 assert auth.login('alice','different-private-password')['enroll'] is False
 with pytest.raises(ValueError):auth.manage_user(admin['id'],'admin','demote','very-long-password',codes[4])
 assert auth.user(admin_token)['admin']
 with auth.connect() as db:
  assert db.execute('SELECT count(*) FROM security_events WHERE action LIKE "admin_%"').fetchone()[0]==4


def test_user_admin_routes_are_role_gated(platform):
 auth=Auth(platform.root)
 admin,token,_=enroll(auth,'admin')
 user,user_token,_=enroll(auth,'alice')
 with TestClient(create_app(platform),base_url='http://localhost') as c:
  assert c.get('/api/users').status_code==401
  c.cookies.set('session',user_token)
  assert c.get('/api/users').status_code==403
  assert c.post('/api/users/manage',json={'action':'promote','username':'alice'}).status_code==403
  c.cookies.set('session',token)
  response=c.get('/api/users');assert response.status_code==200 and len(response.json())==2


def test_shared_agent_uses_current_users_files_connections_and_memory(platform):
 auth=Auth(platform.root)
 alice,_,_=enroll(auth,'alice');bob,_,_=enroll(auth,'bobby')
 a=auth.service(alice);b=auth.service(bob)
 a.config.connections.imap.host='alice-only.example';a.config.connections.imap.username='alice'
 a.registry.save_agent(Agent(name='legal',model='demo:office',instructions='Общий помощник',skills=['office_read']))
 shared=SharedAgents(platform.root)
 id_=shared.publish(alice['id'],a.registry.build('legal'))
 b.registry.scan()
 agent=b.registry.build(id_)
 assert agent.name==id_ and agent.memory.path.startswith('data/shared/')
 assert b.config.connections.imap.host!='alice-only.example'
 a.runner.memory(a.registry.build(id_)).ensure('same','legal','Alice secret')
 assert b.runner.memory(agent).sessions(agent.name)==[]
 with pytest.raises(PermissionError):shared.unpublish(bob['id'],id_)
 with pytest.raises(ValueError):shared.publish(alice['id'],Agent(name='custom',model='demo:office',skills=['email_filter']))
 shared.unpublish(alice['id'],id_)
 with pytest.raises(ValueError):b.registry.build(id_)


def test_files_api_does_not_cross_tenants(platform):
 from superagent.integrations.office_files import OfficeFiles
 from superagent.integrations.office import create_bytes
 auth=Auth(platform.root)
 alice,_,_=enroll(auth,'alice');bob,token,_=enroll(auth,'bobby')
 f=OfficeFiles(auth.service(alice).root).save('private.docx',create_bytes('docx',{'blocks':[{'type':'paragraph','text':'secret'}]}))
 with TestClient(create_app(platform),base_url='http://localhost') as c:
  c.cookies.set('session',token)
  assert c.get(f['url']).status_code==400
  assert c.get('/api/files').json()==[]
  assert c.get('/api/knowledge').json()['documents']==[]


def test_shared_model_transport_never_enters_editable_settings(platform):
 from superagent.settings import update,public
 from superagent.core.providers.factory import make_provider
 from superagent.core.providers.pool import resolve_provider
 from superagent.gateway.config import load_config
 auth=Auth(platform.root)
 admin,_,_=enroll(auth,'admin');bob,_,_=enroll(auth,'bobby')
 a=auth.service(admin)
 update(a,{'providers':{**public(a.config)['providers'],'openai':{'type':'openai','base_url':'https://api.openai.com/v1','api_key':'shared-model-key','api_key_env':None,'models':['gpt-4.1-mini']}}})
 b=auth.service(bob)
 assert b.config.providers['openai'].key()==''
 assert make_provider('openai:gpt-4.1-mini',b.config).api_key=='shared-model-key'
 from superagent.core.providers.base import ProviderError
 with pytest.raises(ProviderError):make_provider('openai:unapproved-model',b.config)
 assert 'shared-model-key' not in str(public(b.config))
 draft=public(b.config)
 draft['providers']['openai']['base_url']='https://attacker.invalid/v1'
 update(b,draft)
 assert make_provider('openai:gpt-4.1-mini',b.config).api_key==''
 assert 'shared-model-key' not in (b.root/'config.yaml').read_text()
 assert load_config(b.root).providers['openai'].key()==''
