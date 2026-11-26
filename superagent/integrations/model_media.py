"""Bounded requests to configured model APIs; provider error bodies are private."""
import base64
import io
import json
import re
from pathlib import Path
from uuid import uuid4

import httpx


def api_error(status,error):
    code=error.get('code') if isinstance(error,dict) else None
    param=error.get('param') if isinstance(error,dict) else None
    if code in ('billing_hard_limit_reached','billing_limit_user_error','insufficient_quota'):
        return 'Достигнут лимит или исчерпана квота API. Проверьте баланс и лимиты расходов в кабинете провайдера.'
    if param=='model' or code in ('model_not_found','model_deprecated','model_not_available'):
        return 'Выбранная модель недоступна или снята с эксплуатации. Обновите каталог и выберите доступную модель в настройках.'
    if code in ('invalid_api_key','authentication_error') or status==401:return 'API отклонил ключ. Проверьте сохранённый ключ и его права.'
    if status==403:return 'Провайдер запретил запрос. Проверьте права ключа, доступ к модели, регион и необходимость подтверждения организации.'
    if status==429:return 'API ограничил запросы. Проверьте квоту и повторите позже.'
    if param in ('size','output_format','quality','n','dimensions','prompt'):
        return 'API не принял параметр '+param+'. Проверьте совместимость параметров с выбранной моделью.'
    return 'API модели отклонил запрос (HTTP '+str(status)+'). Проверьте модель, ключ, баланс и права доступа.'


def model_post(config,provider,path,payload,*,limit=20_000_000,timeout=120):
    from superagent.core.providers.pool import resolve_provider
    from urllib.parse import urlsplit
    cfg=resolve_provider(config,provider)
    if cfg is None or cfg.type!='openai':raise ValueError('Выберите OpenAI-совместимый провайдер в настройках')
    if cfg is config._model_pool.get(provider) and payload.get('model') not in cfg.models:raise ValueError('Модель не разрешена в общем каталоге администратора')
    if not cfg.key() and urlsplit(cfg.base_url).hostname in ('api.openai.com','api.deepseek.com'):raise ValueError('Сохраните API-ключ выбранного провайдера в настройках моделей')
    try:
        with httpx.Client(timeout=timeout,trust_env=False,follow_redirects=False) as client:
            with client.stream('POST',cfg.base_url.rstrip('/')+path,headers={'Authorization':'Bearer '+cfg.key()},json=payload) as response:
                if response.status_code!=200:
                    body=bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body)>64000:break
                    try:error=json.loads(body).get('error',{}) if len(body)<=64000 else {}
                    except (ValueError,AttributeError):error={}
                    raise ValueError('HTTP '+str(response.status_code)+': '+api_error(response.status_code,error))
                body=bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body)>limit:raise ValueError('Ответ API превышает допустимый размер')
        return json.loads(body)
    except (httpx.HTTPError,json.JSONDecodeError):raise ValueError('Не удалось получить ответ API модели: сеть, таймаут или формат ответа') from None


def image_path(root,file_id):
    if not re.fullmatch('[a-f0-9]{32}',file_id):raise ValueError('Недопустимый ID изображения')
    directory=Path(root).resolve()/'data/images'
    path=directory/(file_id+'.png')
    if directory.is_symlink() or not path.resolve().is_relative_to(Path(root).resolve()/'data') or path.is_symlink() or not path.is_file():raise ValueError('Изображение не найдено в вашем пространстве')
    return path


def generate_image(root,config,payload):
    from PIL import Image
    settings=config.image_generation
    if not settings.enabled:raise ValueError('Генерация изображений выключена в настройках')
    from superagent.core.providers.capabilities import RETIRED_IMAGE_MODELS
    from superagent.core.providers.pool import resolve_provider
    from urllib.parse import urlsplit
    provider=resolve_provider(config,settings.provider)
    if provider and urlsplit(provider.base_url).hostname=='api.openai.com' and settings.model in RETIRED_IMAGE_MODELS:raise ValueError('Модель '+settings.model+' снята с эксплуатации. Выберите доступную модель изображений в настройках.')
    prompt=payload['prompt'];size=payload['size']
    if not 1<=len(prompt.strip())<=10000 or size not in ('1024x1024','1536x1024','1024x1536'):raise ValueError('Нужен запрос до 10000 символов и размер 1024x1024, 1536x1024 или 1024x1536')
    directory=Path(root).resolve()/'data/images'
    if directory.is_symlink() or not directory.resolve().is_relative_to(Path(root).resolve()/'data'):raise ValueError('Недопустимое хранилище')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if len(list(directory.glob('*.png')))>=100:raise ValueError('Достигнут предел: 100 изображений')
    result=model_post(config,settings.provider,'/images/generations',{'model':settings.model,'prompt':prompt,'size':size,'n':1,'output_format':'png'},timeout=300)
    try:
        data=base64.b64decode(result['data'][0]['b64_json'],validate=True)
        if len(data)>12_000_000:raise ValueError()
        with Image.open(io.BytesIO(data)) as image:
            if image.format!='PNG' or image.width*image.height>4_000_000:raise ValueError()
            image.verify()
    except (ValueError,KeyError,IndexError,TypeError,OSError):raise ValueError('API не вернул допустимое PNG-изображение') from None
    file_id=uuid4().hex
    path=directory/(file_id+'.png')
    with path.open('xb') as output:path.chmod(0o600);output.write(data)
    return {'file':{'id':file_id,'name':'image-'+file_id[:8]+'.png','extension':'png','size':len(data),'url':'/api/images/'+file_id},'model':settings.model}
