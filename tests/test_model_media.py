import io
import base64
import httpx
import pytest
from PIL import Image
from superagent.integrations.model_media import generate_image,image_path,model_post
from superagent.integrations.knowledge import embed


def test_image_generation_is_saved_privately_and_error_does_not_leak_key(platform,tmp_path,monkeypatch):
 buffer=io.BytesIO();Image.new('RGB',(16,16),'blue').save(buffer,format='PNG')
 requests=[]
 def fake(config,provider,path,payload,**kw):
  requests.append((provider,path,payload));return {'data':[{'b64_json':base64.b64encode(buffer.getvalue()).decode()}]}
 monkeypatch.setattr('superagent.integrations.model_media.model_post',fake)
 result=generate_image(platform.root,platform.config,{'prompt':'Синяя иллюстрация','size':'1024x1024'})
 assert image_path(platform.root,result['file']['id']).read_bytes()==buffer.getvalue()
 assert requests[0][1]=='/images/generations' and requests[0][2]['n']==1
 with pytest.raises(ValueError):image_path(tmp_path/'other',result['file']['id'])
 with pytest.raises(ValueError):generate_image(platform.root,platform.config,{'prompt':'x','size':'unknown'})


def test_model_api_does_not_echo_remote_secret_errors(platform,monkeypatch):
 from pydantic import SecretStr
 platform.config.providers['openai'].api_key=SecretStr('private-api-key')
 client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(401,json={'error':'private-api-key'})))
 monkeypatch.setattr('superagent.integrations.model_media.httpx.Client',lambda **kw:client)
 with pytest.raises(ValueError,match='HTTP 401') as result:model_post(platform.config,'openai','/images/generations',{})
 assert 'private-api-key' not in str(result.value)


def test_embedding_vectors_validate_count_order_and_finite_values(platform,monkeypatch):
 platform.config.embeddings.enabled=True
 monkeypatch.setattr('superagent.integrations.knowledge.model_post',lambda *a,**kw:{'data':[{'index':1,'embedding':[0,2]},{'index':0,'embedding':[3,0]}]})
 assert embed(platform.config,['first','second'])==[[1.,0.],[0.,1.]]
 monkeypatch.setattr('superagent.integrations.knowledge.model_post',lambda *a,**kw:{'data':[{'index':0,'embedding':[float('nan')]}]})
 with pytest.raises(ValueError):embed(platform.config,['first'])


@pytest.mark.parametrize('error,expected',[
 ({'code':'invalid_value','param':'model','message':'private-api-key'},'снята с эксплуатации'),
 ({'code':'billing_hard_limit_reached','message':'private-api-key'},'лимит'),
 ({'code':'unknown_parameter','param':'output_format','message':'private-api-key'},'output_format'),
])
def test_model_errors_are_actionable_without_echoing_remote_messages(platform,monkeypatch,error,expected):
 from pydantic import SecretStr
 platform.config.providers['openai'].api_key=SecretStr('private-api-key')
 client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(400,json={'error':error})))
 monkeypatch.setattr('superagent.integrations.model_media.httpx.Client',lambda **kw:client)
 with pytest.raises(ValueError) as result:model_post(platform.config,'openai','/images/generations',{})
 assert expected in str(result.value) and 'private-api-key' not in str(result.value)


def test_retired_image_model_is_rejected_before_paid_request(platform,monkeypatch):
 platform.config.image_generation.model='gpt-image-1'
 monkeypatch.setattr('superagent.integrations.model_media.model_post',lambda *a,**kw:pytest.fail('Retired model must not be requested'))
 with pytest.raises(ValueError,match='снята'):generate_image(platform.root,platform.config,{'prompt':'test','size':'1024x1024'})
