import copy
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from superagent.integrations.office import create_bytes
from superagent.integrations.office_files import OfficeFiles
from superagent.integrations.knowledge import Knowledge,run_knowledge,signature
from superagent.web.app import create_app


def setup_collections(platform,monkeypatch):
 platform.config.embeddings.enabled=True
 monkeypatch.setattr('superagent.integrations.knowledge.embed',lambda cfg,texts:[[1.,0.] for text in texts])
 store=Knowledge(platform.root)
 a=store.create_collection('Договоры');b=store.create_collection('Проект Б')
 files=[]
 for collection,secret in ((a,'contract-secret'),(b,'project-b-secret')):
  file=OfficeFiles(platform.root).save(secret+'.docx',create_bytes('docx',{'blocks':[{'type':'paragraph','text':secret}]}))
  store.index(platform.config,file['id'],collection['id']);files.append(file)
 return store,a,b,files


def test_collections_filter_and_no_cross_collection_writes(platform,monkeypatch,tmp_path):
 store,a,b,files=setup_collections(platform,monkeypatch)
 result=store.search(platform.config,'secret',10,[a['id']])
 assert {m['collection_id'] for m in result['matches']}=={a['id']}
 assert all('project-b-secret' not in m['text'] for m in result['matches'])
 monkeypatch.setattr('superagent.integrations.knowledge.embed',lambda *a:pytest.fail('Off/unapproved scope must not call embedding API'))
 assert store.search(platform.config,'secret',10,[])['matches']==[]
 for name,payload in [('knowledge_index',{'file_id':files[1]['id'],'collection_id':b['id']}),('knowledge_forget',{'file_id':files[1]['id'],'collection_id':b['id']})]:
  with pytest.raises(PermissionError):run_knowledge(platform.root,platform.config,name,payload,[a['id']])
 assert store.list([b['id']])['documents'][0]['file_id']==files[1]['id']
 with pytest.raises(ValueError):Knowledge(tmp_path/'other').validate_collections([a['id']])


class Recorder:
 def __init__(self):self.calls=[]
 def chat(self,messages,tools):
  self.calls.append((copy.deepcopy(messages),copy.deepcopy(tools)))
  return {'role':'assistant','content':'ready'}


def test_chat_context_is_persisted_and_off_removes_tools(platform,monkeypatch):
 store,a,b,files=setup_collections(platform,monkeypatch)
 provider=Recorder()
 events=list(platform.runner.run_conversation('use contracts','demo:office',knowledge=[a['id']],provider=provider))
 session=events[0]['session_id']
 assert events[0]['knowledge_collections']==[a['id']]
 assert 'knowledge_search' in {t['function']['name'] for t in provider.calls[-1][1]}
 list(platform.runner.run_conversation('continue','demo:office',session_id=session,provider=provider))
 assert platform.runner.conversation_memory().knowledge(session)==[a['id']]
 list(platform.runner.run_conversation('switch context','demo:office',session_id=session,knowledge=[b['id']],provider=provider))
 assert platform.runner.conversation_memory().knowledge(session)==[b['id']]
 assert a['id'] not in provider.calls[-1][0][-2]['content']
 list(platform.runner.run_conversation('off','demo:office',session_id=session,knowledge=[],provider=provider))
 assert not any(t['function']['name'].startswith('knowledge_') for t in provider.calls[-1][1])
 assert platform.runner.conversation_memory().knowledge(session)==[]
 fresh=list(platform.runner.run_conversation('new chat','demo:office',provider=provider))
 assert fresh[0]['knowledge_collections']==[]


def test_scoped_tool_result_never_contains_other_context(platform,monkeypatch):
 store,a,b,files=setup_collections(platform,monkeypatch)
 class Caller:
  def __init__(self):self.step=0
  def chat(self,messages,tools):
   self.step+=1
   if self.step==1:return {'role':'assistant','content':None,'tool_calls':[{'id':'query','type':'function','function':{'name':'knowledge_search','arguments':json.dumps({'query':'secret','limit':10})}}]}
   result=json.loads(messages[-1]['content'])
   assert {m['collection_id'] for m in result['matches']}=={a['id']}
   assert 'project-b-secret' not in messages[-1]['content']
   return {'role':'assistant','content':'Scoped result'}
 assert list(platform.runner.run_conversation('search','demo:office',knowledge=[a['id']],provider=Caller(),confirm=lambda _:True))[-1]['content']=='Scoped result'


def test_legacy_vectors_migrate_into_default_collection(platform):
 path=platform.root/'data/knowledge.db'
 with sqlite3.connect(path) as db:
  db.execute('CREATE TABLE chunks(file TEXT,part INTEGER,name TEXT,text TEXT,vector TEXT,signature TEXT,created REAL,PRIMARY KEY(file,part))')
  db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',('a'*32,0,'old.docx','legacy knowledge','[1,0]',signature(platform.config),1))
 store=Knowledge(platform.root)
 assert store.list(['default'])['documents'][0]['name']=='old.docx'
 with store.connect() as db:assert db.execute('SELECT collection,text FROM chunks').fetchone()==('default','legacy knowledge')


def test_knowledge_graph_uses_saved_vectors_and_respects_selected_collection(platform, monkeypatch):
    store, a, b, files = setup_collections(platform, monkeypatch)
    with TestClient(create_app(platform, authentication=False), base_url='http://localhost') as client:
        monkeypatch.setattr('superagent.integrations.knowledge.embed', lambda *args: pytest.fail('Graph called embedding API'))
        response = client.get('/api/knowledge/graph')
        assert response.status_code == 200
        full = response.json()
        assert full['total_documents'] == 2
        assert {edge['type'] for edge in full['edges']} == {'contains', 'similar'}
        assert all('vector' not in node and 'text' not in node for node in full['nodes'])
        local = client.get('/api/knowledge/graph', params={'collection': a['id']}).json()
        assert {node['file_id'] for node in local['nodes'] if node['type'] == 'document'} == {files[0]['id']}
        assert {edge['type'] for edge in local['edges']} == {'contains'}
        assert client.get('/api/knowledge/graph?collection=unknown').status_code == 400


def test_knowledge_buttons_api_confirmation_and_dialog_settings(platform,monkeypatch):
 platform.config.embeddings.enabled=True
 monkeypatch.setattr('superagent.integrations.knowledge.embed',lambda cfg,texts:[[1.,0.] for _ in texts])
 with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as client:
  c=client.post('/api/knowledge/collections',json={'name':'Законодательство','description':'Личная подборка'}).json()
  url='/api/knowledge/collections/'+c['id']
  assert client.post(url+'/note',json={'title':'Статья','text':'Пример','approved':False}).status_code==400
  response=client.post(url+'/note',json={'title':'Личные заметки','text':'Текст для проверки','approved':True})
  assert response.status_code==200
  file=response.json()['file_id']
  assert client.get(url).json()['documents'][0]['file_id']==file
  platform.runner.conversation_memory().ensure('dialog','__conversation__','Test')
  assert client.put('/api/conversations/dialog/settings',json={'knowledge_collections':[c['id']]}).status_code==200
  assert client.get('/api/conversations/dialog/settings').json()['knowledge_collections']==[c['id']]
  assert client.put('/api/conversations/dialog/settings',json={'knowledge_collections':['foreign']}).status_code==400
  assert client.request('DELETE',url,json={'approved':False}).status_code==400
  assert client.request('DELETE',url,json={'approved':True}).status_code==200
  assert client.get('/api/conversations/dialog/settings').json()['knowledge_collections']==[]
  assert OfficeFiles(platform.root).path(file).exists()
