from __future__ import annotations

import asyncio
import base64
import contextvars
import json
import queue
import threading
import time
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import unquote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from superagent.gateway.models import Agent, StrictModel
from superagent.gateway.config import EmbeddingConfig
from superagent.gateway.registry import parse_skill
from superagent.integrations.office_files import MAX_FILE, OfficeFiles
from superagent.service import Platform
from superagent.schedules import Schedule, Store, Scheduler, validate_target

OFFICE_BODY_TYPE = 'application/octet-stream'
LEGACY_OFFICE_BODY_LIMIT = 11_000_000
OFFICE_BODY_ERROR = f'Документ превышает лимит загрузки. Загружайте файлы до {MAX_FILE // 1_000_000} МБ.'


class SkillDraft(StrictModel):
    name: str
    metadata: str = Field(max_length=100_000)
    code: str = Field(max_length=1_000_000)


class SkillTest(SkillDraft):
    input: dict = Field(default_factory=dict)
    confirmed: bool = False


class WebSetting(StrictModel):
    web_enabled: bool | None = None
    knowledge_collections: list[str] | None = Field(default=None,max_length=20)


class ChatRequest(StrictModel):
    agent: str | None = None
    conversation: bool = False
    connections: list[str] | None = Field(default=None, max_length=8)
    model: str = Field(default="demo:office", pattern=r"^[a-z][a-z0-9_-]*:.+$")
    message: str = Field(min_length=1, max_length=100_000)
    transport: Literal["stream", "poll"] = "stream"
    web_enabled: bool | None = None
    knowledge_collections: list[str] | None = Field(default=None,max_length=20)
    images: list[str] = Field(default_factory=list, max_length=4)
    files: list[str] = Field(default_factory=list,max_length=4)

    @field_validator("images")
    @classmethod
    def validate_images(cls, values):
        from superagent.core.images import validate_images
        return validate_images(values)

    session_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class ImportConversation(StrictModel):
    agent: str
    session_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')


class Decision(StrictModel):
    approved: bool


class Feedback(StrictModel):
    rating: Literal['good', 'bad'] | None


@dataclass
class Pending:
    details: dict
    deadline: float
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    cancelled: threading.Event | None = None
    owner: str | None = None


def create_app(platform: Platform | None = None, root: Path | None = None, *, authentication: bool = True):
    from superagent.auth import Auth, ServiceProxy, current_service
    owner = platform or Platform(root or Path.cwd())
    auth = Auth(owner.root) if authentication else None
    service = ServiceProxy() if auth else owner
    pending: dict[str, Pending] = {}
    pending_lock = threading.Lock()
    registry_lock = threading.RLock()
    model_discoveries = {}
    model_generations = {}
    tests_lock = threading.Semaphore(2)
    streams_lock = threading.Lock()
    active_streams: set[threading.Event] = set()
    stream_users = {}
    poll_runs = {}

    def revoke_streams(uid):
        with streams_lock:
            for cancel, owner_id in list(stream_users.items()):
                if owner_id == uid: cancel.set()
    static = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app):
        scheduler = Scheduler(owner, auth)
        scheduler.start()
        from superagent.channel_runtime import ChannelSupervisor
        channel_manager=ChannelSupervisor(owner,auth)
        app.state.channel_manager=channel_manager
        channel_manager.start()
        yield
        channel_manager.close()
        scheduler.close()
        with streams_lock:
            for cancellation in active_streams:
                cancellation.set()
        with pending_lock:
            for item in pending.values():
                item.event.set()
        if platform is None:
            owner.close()
        if auth:
            for tenant in auth.services.values(): tenant.close()

    app = FastAPI(title="aiagent", lifespan=lifespan)
    app.state.platform = owner
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=owner.config.web_hosts)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin != expected:
            return JSONResponse({"detail": "Cross-origin requests are forbidden"}, status_code=403)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
            office_upload = request.method == 'POST' and request.url.path == '/api/files'
            if office_upload and media_type == OFFICE_BODY_TYPE:
                maximum = MAX_FILE
            elif media_type.startswith('application/json'):
                maximum = LEGACY_OFFICE_BODY_LIMIT if office_upload else 2_000_000
            else:
                return JSONResponse({"detail": "application/json required"}, status_code=415)
            length = request.headers.get("content-length")
            if length and int(length) > maximum:
                detail = OFFICE_BODY_ERROR if office_upload and media_type == OFFICE_BODY_TYPE else 'Request too large'
                return JSONResponse({'detail': detail}, status_code=413)
        context_token = None
        if auth and request.url.path.startswith('/api/') and not request.url.path.startswith('/api/auth/'):
            user = auth.user(request.cookies.get('session'))
            if not user:
                return JSONResponse({'detail':'Требуется вход'},status_code=401)
            request.state.user = user
            context_token = current_service.set(auth.service(user))
        try:
            response = await call_next(request)
        finally:
            if context_token is not None: current_service.reset(context_token)
        if request.url.path=='/' or request.url.path.startswith("/api/auth/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc) if auth else service.audit.redact(str(exc))}, status_code=400)

    @app.exception_handler(PermissionError)
    async def permission_error(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=403)

    from superagent.core.providers.base import ProviderError
    @app.exception_handler(ProviderError)
    async def provider_error(request,exc):
        return JSONResponse({'detail':service.audit.redact(str(exc))},status_code=400)

    def require_admin(request):
        if auth is None or not getattr(request.state,'user',{}).get('admin'):raise HTTPException(403,'Только администратор')

    @app.get('/api/auth/state')
    def auth_state(request: Request):
        if not auth: return {'authenticated':True}
        with auth.connect() as db: setup = db.execute('SELECT count(*) FROM users').fetchone()[0] == 0
        user=auth.user(request.cookies.get('session'))
        return {'setup':setup,'authenticated':bool(user),'user':user}

    @app.post('/api/auth/setup')
    def setup(draft: dict):
        return auth.create(draft.get('username',''),draft.get('password',''),bootstrap=True)

    @app.post('/api/auth/login')
    def login(draft: dict):
        return auth.login(draft.get('username',''),draft.get('password',''))

    @app.post('/api/auth/verify')
    def verify(draft: dict, request: Request):
        token,codes=auth.verify(draft.get('challenge',''),draft.get('code',''))
        response=JSONResponse({'ok':True, 'recovery_codes':codes})
        response.set_cookie('session',token,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=43200)
        return response

    @app.post('/api/auth/logout')
    def logout(request: Request):
        with auth.connect() as db:
            db.execute('DELETE FROM sessions WHERE token=?',(auth.digest(request.cookies.get('session','')),))
        response=JSONResponse({'ok':True}); response.delete_cookie('session'); return response

    @app.post('/api/auth/recovery')
    def recovery(request: Request, draft: dict):
        user = auth.user(request.cookies.get('session'))
        if not user: raise HTTPException(401, 'Требуется вход')
        return {'recovery_codes': auth.rotate_recovery(user['id'], draft.get('password',''), draft.get('code',''))}

    @app.post('/api/auth/password')
    def change_password(request: Request, draft: dict):
        user = auth.user(request.cookies.get('session'))
        if not user: raise HTTPException(401, 'Требуется вход')
        auth.change_password(user['id'], draft.get('password',''), draft.get('code',''), draft.get('new_password',''))
        revoke_streams(user['id'])
        response=JSONResponse({'ok':True}); response.delete_cookie('session'); return response

    @app.post('/api/users/reset-2fa')
    def reset_two_factor(request: Request, draft: dict):
        require_admin(request)
        uid = auth.reset_two_factor(request.state.user['id'],draft.get('username',''),draft.get('password',''),draft.get('code',''))
        revoke_streams(uid)
        return {'ok':True}

    @app.post('/api/users')
    def create_user(request: Request, draft: dict):
        require_admin(request)
        result=auth.create(draft.get('username',''),draft.get('password',''))
        with auth.connect() as db:
            target=db.execute('SELECT id FROM users WHERE username=?',(result['username'],)).fetchone()[0]
            auth.security_event(db,request.state.user['id'],target,'admin_user_created')
        return result

    @app.get('/api/users')
    def list_users(request: Request):
        require_admin(request)
        return auth.list_users(request.state.user['id'])

    @app.post('/api/users/manage')
    def manage_user(request: Request,draft: dict):
        require_admin(request)
        uid=auth.manage_user(request.state.user['id'],draft.get('username',''),draft.get('action',''),draft.get('password',''),draft.get('code',''),new_password=draft.get('new_password'))
        revoke_streams(uid)
        return {'ok':True}

    @app.get('/api/schedules')
    def schedules():
        return Store(service.root).list()

    @app.post('/api/schedules')
    def add_schedule(draft: Schedule):
        validate_target(service.registry, draft)
        return {'id': Store(service.root).save(draft)}

    @app.put('/api/schedules/{id_}')
    def edit_schedule(id_: str, draft: Schedule):
        validate_target(service.registry, draft)
        return {'id': Store(service.root).save(draft, id_)}

    @app.post('/api/schedules/{id_}/toggle')
    def toggle_schedule(id_: str, draft: Decision):
        Store(service.root).toggle(id_, draft.approved)
        return {'ok': True}

    @app.delete('/api/schedules/{id_}')
    def delete_schedule(id_: str):
        Store(service.root).delete(id_)
        return {'ok': True}

    @app.get('/api/channels/status')
    def channel_status():
        manager=getattr(app.state,'channel_manager',None)
        return manager.status(service) if manager else []

    @app.get('/api/files')
    def list_files():
        return OfficeFiles(service.root).list()

    @app.post('/api/files')
    async def upload_file(request: Request):
        media_type = request.headers['content-type'].split(';', 1)[0].strip().lower()
        binary = media_type == OFFICE_BODY_TYPE
        maximum = MAX_FILE if binary else LEGACY_OFFICE_BODY_LIMIT
        body = bytearray()
        async for chunk in request.stream():
            if len(chunk) > maximum - len(body):
                raise HTTPException(413, OFFICE_BODY_ERROR if binary else 'Request too large')
            body.extend(chunk)
        if binary:
            data = body
            name = unquote(request.headers.get('x-office-filename', ''), errors='strict')
        else:
            try:
                draft = json.loads(body)
                data = base64.b64decode(draft['data'], validate=True)
                name = draft['name']
            except (ValueError, KeyError, TypeError):
                raise ValueError('Нужны имя файла и содержимое base64') from None
        # ZIP/XML parsing runs outside the event loop.
        return await asyncio.to_thread(OfficeFiles(service.root).save,name,data)

    @app.get('/api/files/{file_id}/download')
    def download_file(file_id: str):
        from superagent.integrations.office_files import MIME
        store=OfficeFiles(service.root)
        info=store.info(file_id)
        return FileResponse(store.path(file_id),filename=info['name'],media_type=MIME[info['extension']],headers={'Cache-Control':'private, no-store'})

    @app.get('/api/images/{file_id}')
    def download_image(file_id: str):
        from superagent.integrations.model_media import image_path
        return FileResponse(image_path(service.root,file_id),media_type='image/png',headers={'Cache-Control':'private, no-store'})

    @app.get('/api/knowledge')
    def knowledge_status():
        from superagent.integrations.knowledge import Knowledge
        return Knowledge(service.root).list()

    @app.get('/api/knowledge/collections')
    def knowledge_collections():
        from superagent.integrations.knowledge import Knowledge
        return Knowledge(service.root).collections()

    @app.get('/api/knowledge/graph')
    def knowledge_graph(collection: str = Query('', max_length=32), offset: int = Query(0, ge=0, le=5000)):
        from superagent.integrations.knowledge import Knowledge
        from superagent.integrations.knowledge_graph import graph
        with service.audit.track('__knowledge__', 'skill', 'knowledge_graph'):
            return JSONResponse(graph(Knowledge(service.root), collection or None, offset=offset),
                                headers={'Cache-Control': 'no-store'})

    @app.post('/api/knowledge/collections')
    def create_knowledge_collection(draft: dict):
        from superagent.integrations.knowledge import Knowledge
        return Knowledge(service.root).create_collection(draft.get('name'),draft.get('description',''))

    @app.get('/api/knowledge/collections/{collection_id}')
    def knowledge_collection(collection_id: str):
        from superagent.integrations.knowledge import Knowledge
        return Knowledge(service.root).list([collection_id])

    @app.delete('/api/knowledge/collections/{collection_id}')
    def delete_knowledge_collection(collection_id: str,draft: Decision):
        if not draft.approved:raise ValueError('Удаление требует подтверждения')
        from superagent.integrations.knowledge import Knowledge
        Knowledge(service.root).delete_collection(collection_id)
        return {'ok':True}

    @app.put('/api/knowledge/config')
    def knowledge_config(draft: EmbeddingConfig):
        from superagent.core.providers.pool import resolve_provider
        from superagent.settings import update
        provider=resolve_provider(service.config,draft.provider)
        if provider is None or provider.type!='openai':raise ValueError('Выберите OpenAI-совместимый провайдер embeddings')
        with registry_lock:update(service,{'embeddings':draft.model_dump()})
        return draft.model_dump()

    @app.post('/api/knowledge/collections/{collection_id}/index')
    def index_knowledge_file(collection_id: str,draft: dict):
        if draft.get('approved') is not True:raise ValueError('Нужно подтвердить передачу текста embedding-провайдеру')
        from superagent.integrations.knowledge import Knowledge
        with service.audit.track('__knowledge__', 'skill', 'knowledge_index'):
            result = Knowledge(service.root).index(service.config.model_copy(deep=True), draft.get('file_id'), collection_id)
        service.audit.record(agent='__knowledge__',kind='skill',skill='knowledge_index',input={'collection_id':collection_id,'file_id':draft.get('file_id')},output=result)
        return result

    @app.delete('/api/knowledge/collections/{collection_id}/documents/{file_id}')
    def forget_knowledge_file(collection_id: str,file_id: str,draft: Decision):
        if not draft.approved:raise ValueError('Удаление требует подтверждения')
        from superagent.integrations.knowledge import Knowledge
        return Knowledge(service.root).forget(file_id,collection_id)

    @app.post('/api/knowledge/collections/{collection_id}/note')
    def add_knowledge_note(collection_id: str,draft: dict):
        if draft.get('approved') is not True:raise ValueError('Нужно подтвердить передачу текста embedding-провайдеру')
        from superagent.integrations.knowledge import Knowledge
        from superagent.integrations.office_files import OfficeFiles
        from superagent.integrations.office import create_bytes
        title,text=draft.get('title',''),draft.get('text','')
        if not isinstance(title,str) or not 1<=len(title.strip())<=100 or not isinstance(text,str) or not 1<=len(text.strip())<=50000:raise ValueError('Заголовок: до 100 символов; текст: до 50000')
        store=Knowledge(service.root);store.validate_collections([collection_id])
        if not service.config.embeddings.enabled:raise ValueError('Сначала включите embedding-модель')
        filename=''.join('-' if c in '/\\\r\n\x00' else c for c in title.strip())+'.docx'
        file=OfficeFiles(service.root).save(filename,create_bytes('docx',{'title':title,'blocks':[{'type':'paragraph','text':text[i:i+2000]} for i in range(0,len(text),2000)]}))
        with service.audit.track('__knowledge__', 'skill', 'knowledge_index'):
            result = store.index(service.config.model_copy(deep=True), file['id'], collection_id)
        service.audit.record(agent='__knowledge__',kind='skill',skill='knowledge_index',input={'collection_id':collection_id,'file_id':file['id']},output=result)
        return result

    def shared_store():
        from superagent.shared_agents import SharedAgents
        return SharedAgents(owner.root)

    @app.get('/api/shared-agents')
    def list_shared_agents(request: Request):
        user=getattr(request.state,'user',{})
        return [{**row,'can_manage':row['owner']==user.get('id') or bool(user.get('admin'))} for row in shared_store().list()]

    @app.post('/api/shared-agents')
    def publish_agent(request: Request,draft: dict):
        if auth is None:raise HTTPException(403,'Нужна авторизация')
        agent=service.registry.build(draft.get('name',''))
        id_=shared_store().publish(request.state.user['id'],agent)
        service.registry.scan()
        return {'id':id_}

    @app.delete('/api/shared-agents/{id_}')
    def unpublish_agent(id_: str,request: Request):
        if auth is None:raise HTTPException(403,'Нужна авторизация')
        shared_store().unpublish(request.state.user['id'],id_,bool(request.state.user['admin']))
        service.registry.scan()
        return {'ok':True}

    @app.post('/api/channels/{name}/test')
    def channel_test(name: str):
        from superagent.channels import test_channel
        if name not in service.config.channels:raise ValueError('Канал не найден')
        try:return test_channel(service,service.config.channels[name])
        except ValueError:raise
        except Exception:raise ValueError('Не удалось подключиться к каналу. Проверьте адрес, токен и доступ бота.') from None

    @app.get("/api/settings")
    def settings():
        from superagent.settings import public
        return public(service.config)

    @app.get('/api/provider-presets')
    def provider_presets():
        from superagent.gateway.provider_presets import PRESETS
        return PRESETS

    @app.get('/api/providers/capabilities')
    def provider_capabilities():
        from superagent.core.providers.pool import effective_providers
        from superagent.core.providers.capabilities import image_models
        return {name:{'type':p.type,'image_models':image_models(p),'embedding_models':[m for m in p.models if 'embed' in m.lower()]} for name,p in effective_providers(service.config).items()}

    @app.put("/api/settings")
    def save_settings(draft: dict):
        from superagent.settings import update
        with registry_lock:
            return update(service, draft)

    @app.get('/api/connections/settings')
    def source_settings():
        from superagent.settings import public
        settings = public(service.config)
        return {key: settings[key] for key in ('connections', 'channels')}

    @app.put('/api/connections/settings')
    def save_source_settings(draft: dict):
        if draft.keys() - {'connections', 'channels'}:
            raise ValueError('Здесь можно изменить только источники и мессенджеры')
        from superagent.settings import update
        with registry_lock:
            settings = update(service, draft)
        return {key: settings[key] for key in ('connections', 'channels')}

    @app.post("/api/providers/test")
    def test_provider(draft: dict):
        from superagent.core.providers.factory import make_provider
        selected=draft.get('model','demo:office')
        from superagent.core.providers.pool import resolve_provider
        cfg=resolve_provider(service.config,selected.split(':',1)[0])
        if cfg and cfg.type=='openai' and cfg.base_url.startswith('https://api.openai.com') and not cfg.key():raise ValueError('API-ключ OpenAI не сохранён. Введите его в карточке модели и дождитесь сообщения о сохранении ключа.')
        model = make_provider(selected, service.config)
        try:
            with service.audit.track('__test__', 'model'):
                result = model.chat([{"role": "user", "content": "Reply OK"}], [])
            return {"ok": True, "response": result.get("content")}
        except Exception as exc:
            import re
            status=re.search(r'HTTP (\d{3})',str(exc))
            errors={'401':'API отклонил ключ: проверьте, что он действителен и сохранён.','403':'API запретил запрос: проверьте права ключа и доступность сервиса из региона сервера.','429':'API ограничил запросы: проверьте лимиты, квоту и баланс.','400':'API отклонил параметры: проверьте поддержку чата выбранной моделью.'}
            raise ValueError(errors.get(status[1],'Не удалось подключиться к модели. Проверьте адрес, ключ и имя модели') if status else 'Нет ответа модели: проверьте сеть, адрес API и время ожидания.') from None

    @app.post('/api/providers/models')
    def discover_models(draft: dict):
        from superagent.core.providers.discovery import list_models
        name, base = draft.get('name'), draft.get('base_url')
        from superagent.gateway.models import NAME
        import re
        if not isinstance(name,str) or not re.fullmatch(NAME,name):
            raise ValueError('Некорректное имя провайдера')
        from superagent.gateway.model_lists import snapshot
        with registry_lock:
            expected = snapshot(service.config,name)
            scope = (str(service.root),name)
            generation = uuid4().hex
            if len(model_generations) >= 128: model_generations.pop(next(iter(model_generations)))
            model_generations[scope] = generation
        key = draft.get('api_key') or ''
        if not isinstance(base, str) or not isinstance(key, str) or len(key) > 10000:
            raise ValueError('Некорректные настройки провайдера')
        if not key:
            stored = service.config.providers.get(name) if isinstance(name, str) else None
            if stored is not None and stored.base_url.rstrip('/') == base.rstrip('/'):
                key = stored.key()
        key_to_save=key
        if not key:
            from superagent.core.providers.pool import resolve_provider
            shared=resolve_provider(service.config,name)
            if shared and shared.base_url.rstrip('/')==base.rstrip('/'):key=shared.key()
        models = list_models(base, key)
        ticket = uuid4().hex
        with registry_lock:
            now = time.monotonic()
            for old, value in list(model_discoveries.items()):
                if value['expires'] < now: model_discoveries.pop(old,None)
            if len(model_discoveries) >= 128:
                model_discoveries.pop(next(iter(model_discoveries)))
            model_discoveries[ticket] = {'root':str(service.root),'name':name,'base':base,'models':models,'key':key_to_save,'expected':expected,'generation':generation,'expires':now+120}
        return JSONResponse({'models': models, 'save_ticket':ticket}, headers={'Cache-Control': 'no-store'})

    @app.post('/api/providers/models/save')
    def persist_discovered_models(draft: dict):
        from superagent.gateway.model_lists import save_models
        ticket = draft.get('ticket')
        if not isinstance(ticket,str): raise ValueError('Некорректный запрос сохранения')
        with registry_lock:
            result = model_discoveries.get(ticket)
            if result is None or result['root'] != str(service.root) or result['expires'] < time.monotonic():
                raise ValueError('Запрос сохранения истёк. Обновите список моделей.')
            model_discoveries.pop(ticket)
            if model_generations.get((str(service.root),result['name'])) != result['generation']:
                raise ValueError('Начата более новая загрузка. Старый список не сохранён.')
            save_models(service,result['name'],result['base'],result['models'],result['expected'],key=result['key'])
        return JSONResponse({'saved':True,'models':result['models']}, headers={'Cache-Control':'no-store'})

    @app.get('/api/connection-examples')
    def connection_examples():
        from superagent.integrations.catalog import EXAMPLES
        examples = {**EXAMPLES, 'web_read': {'url': 'https://example.com'}}
        examples['imap_read'] = {'folder': next(iter(service.config.connections.imap.folders), 'INBOX'), 'query': 'ALL', 'limit': 5}
        return examples

    @app.post("/api/connections/{name}/test")
    def connection_test(name: str, draft: dict):
        from superagent.integrations.catalog import BUILTINS
        if name not in BUILTINS:
            raise ValueError("Неизвестное подключение")
        from superagent.integrations.task_writes import WRITE_TOOLS
        if name in WRITE_TOOLS: raise ValueError("Запись требует предпросмотра и подтверждения в чате или CLI")
        if name == "ssh_run": raise ValueError("Выполняйте SSH через агента с подтверждением команды")
        with service.audit.track('__test__', 'skill', name):
            result = service.runner.builtins.run(name, draft)
        service.audit.record(agent="__test__", kind="skill", skill=name, input=draft, output=result)
        return result

    @app.get('/api/agent-templates/work_manager')
    def work_manager_template():
        from superagent.gateway.agent_templates import work_manager
        models=[f'{name}:{model}' for name,cfg in service.config.providers.items() if cfg.type!='demo' and (cfg.key() or cfg.type=='ollama') for model in cfg.models]
        return work_manager(models[0] if models else 'openai:gpt-4o-mini').model_dump()

    @app.get('/api/agent-templates/security_auditor')
    def security_auditor_template():
        from superagent.gateway.agent_templates import security_auditor
        return security_auditor().model_dump()

    @app.get('/api/agent-templates/lawyer_rf')
    def lawyer_template():
        import yaml
        return Agent.model_validate(yaml.safe_load((owner.root/'agents/lawyer_rf.yaml').read_text())).model_dump()

    @app.get('/api/agent-templates/accountant_1c')
    def accountant_template():
        import yaml
        return Agent.model_validate(yaml.safe_load((owner.root/'agents/accountant_1c.yaml').read_text())).model_dump()

    @app.get("/api/workspace")
    def workspace(request: Request):
        with registry_lock:
            service.registry.scan()
            from superagent.core.chat_connections import available
            from superagent.core.providers.capabilities import supports_chat
            from superagent.core.providers.pool import effective_providers
            providers=effective_providers(service.config)
            return {"current_user":getattr(request.state,'user',None),"chat_connections": available(service.config), "shared_agents":shared_store().list(),"agents": [a.model_dump() for a in service.registry.agents.values()],
                    "skills": [s.model_dump() for s in service.registry.skills.values()],
                    "errors": service.registry.errors,
                    "builtin_skills": list(__import__("superagent.integrations.catalog", fromlist=["BUILTINS"]).BUILTINS),
                    "models": [f"{name}:{m}" for name, p in providers.items() for m in p.models if supports_chat(p,m)],
                    "providers": [{"name": name, "type": p.type, "configured": bool(p.key()) or p.type in ("demo", "ollama"),'shared':name in service.config._model_pool and p is service.config._model_pool[name]} for name, p in providers.items()]}

    @app.put("/api/agents/{name}")
    def save_agent(name: str, agent: Agent):
        if name.startswith('shared_'):raise ValueError('Общего агента изменяет автор через публикацию личной версии. Для своей версии выберите другое имя.')
        if name != agent.name:
            raise ValueError("Agent name must match URL")
        with registry_lock:
            service.registry.save_agent(agent)
        return agent.model_dump()

    @app.post("/api/agents/{name}/build")
    def build_agent(name: str):
        with registry_lock:
            service.registry.scan()
            agent = service.registry.build(name)
        return {"ok": True, "agent": agent.model_dump()}

    @app.get("/api/skills/{name}")
    def get_skill(name: str):
        _, metadata, code = service.registry.read_skill(name)
        return {"name": name, "metadata": metadata, "code": code}

    @app.put("/api/skills/{name}")
    def save_skill(name: str, draft: SkillDraft):
        if name != draft.name:
            raise ValueError("Skill name must match URL")
        with registry_lock:
            service.registry.save_skill(name, draft.metadata, draft.code)
        return {"ok": True}

    @app.post("/api/skills/test")
    def test_skill(draft: SkillTest):
        from superagent.integrations.task_writes import WRITE_TOOLS
        if draft.name in WRITE_TOOLS: raise ValueError("Запись нельзя тестировать без предпросмотра и подтверждения в чате или CLI")
        skill = parse_skill(draft.metadata)
        if draft.name != skill.name:
            raise ValueError("Skill name must match metadata")
        protected = skill.irreversible or skill.permissions.subprocess or skill.permissions.filesystem or skill.permissions.network
        if protected and not draft.confirmed:
            raise HTTPException(409, {"confirmation_required": True, "permissions": skill.permissions.model_dump()})
        if not tests_lock.acquire(blocking=False):
            raise HTTPException(429, "Too many concurrent tests")
        started = time.monotonic()
        try:
            with service.audit.track('__test__', 'skill', skill.name):
                result = service.runner.executor.run(skill, draft.input, draft.code)
            status = "ok"
        except Exception as exc:
            result, status = {"error": service.audit.redact(str(exc))}, "error"
        finally:
            tests_lock.release()
        service.audit.record(agent="__test__", kind="skill", skill=skill.name, input=draft.input, output=result, status=status, duration_ms=(time.monotonic()-started)*1000)
        return {"status": status, "output": result}

    @app.get("/api/audit")
    def audit(agent: str | None = None, skill: str | None = None, status: str | None = None, date: str | None = None,
              limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        return service.audit.query(agent, skill, status, date, limit, offset)

    @app.get('/api/audit/overview')
    def audit_overview(request: Request, scope: Literal['mine', 'all'] = 'mine', account: str = Query('', max_length=41),
                       agent: str = Query('', max_length=120), skill: str = Query('', max_length=120),
                       status: str = Query('', max_length=32), date: str = Query('', pattern=r'^(\d{4}-\d{2}-\d{2})?$'),
                       limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)):
        if scope == 'all':
            if not auth:
                raise PermissionError('Только администратор')
            from superagent.audit.overview import overview
            result = overview(auth, request.state.user['id'], account=account, agent=agent, skill=skill,
                              status=status, date=date, limit=limit, offset=offset)
        else:
            result = {'events': service.audit.query(agent, skill, status, date, limit, offset),
                      'active': service.audit.active(), 'accounts': []}
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @app.get('/api/audit/request')
    def audit_request(request: Request, account: str = Query(pattern=r'^[a-z][a-z0-9_]{2,40}$'),
                      event_id: int = Query(ge=1), guest: str = Query('', pattern=r'^([a-f0-9]{64})?$')):
        if not auth:
            raise PermissionError('Только администратор')
        from superagent.audit.overview import request_text
        text = request_text(auth, request.state.user['id'], account, event_id, guest)
        if text is None:
            raise HTTPException(404, 'Запрос не найден')
        return JSONResponse({'text': text}, headers={'Cache-Control': 'no-store'})

    @app.get('/api/conversations')
    def conversations():
        memory = service.runner.conversation_memory()
        items = [{**row, 'scope': 'conversation'} for row in memory.sessions('__conversation__')]
        service.registry.scan()
        for agent in service.registry.agents.values():
            items.extend({**row, 'scope': 'agent'} for row in service.runner.memory(agent).sessions(agent.name))
        return sorted(items, key=lambda row: row['created'], reverse=True)[:100]

    @app.post('/api/conversations/import')
    def import_conversation(draft: ImportConversation):
        from superagent.core.conversations import import_agent_history
        return {'session_id': import_agent_history(service, draft.agent, draft.session_id)}

    @app.get('/api/conversations/{session_id}/settings')
    def conversation_settings(session_id: str):
        from superagent.integrations.knowledge import Knowledge
        memory=service.runner.conversation_memory()
        available={c['id'] for c in Knowledge(service.root).collections()}
        return {'web_enabled': memory.web(session_id),'knowledge_collections':[id_ for id_ in memory.knowledge(session_id) if id_ in available]}

    @app.put('/api/conversations/{session_id}/settings')
    def update_conversation_settings(session_id: str, draft: WebSetting):
        memory=service.runner.conversation_memory()
        with memory.connect() as db:
            if not db.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone():
                raise HTTPException(404, 'Dialog not found')
        if draft.knowledge_collections is not None:
            from superagent.integrations.knowledge import Knowledge
            memory.knowledge(session_id,Knowledge(service.root).validate_collections(draft.knowledge_collections))
        return {'web_enabled': memory.web(session_id, draft.web_enabled),'knowledge_collections':memory.knowledge(session_id)}

    @app.get('/api/conversations/{session_id}')
    def conversation_history(session_id: str):
        return service.runner.conversation_memory().load(session_id, include_message_ids=True)

    def save_response_feedback(memory, agent, session_id, message_id, draft):
        if not 1 <= message_id <= 2**63 - 1 or not memory.save_feedback(session_id, agent, message_id, draft.rating):
            raise HTTPException(404, 'Ответ в этом диалоге не найден')
        return {'rating': draft.rating}

    @app.put('/api/conversations/{session_id}/messages/{message_id}/feedback')
    def conversation_feedback(session_id: str, message_id: int, draft: Feedback):
        return save_response_feedback(service.runner.conversation_memory(), '__conversation__', session_id, message_id, draft)

    @app.get("/api/sessions/{agent}")
    def sessions(agent: str):
        entity = service.registry.build(agent)
        return service.runner.memory(entity).sessions(agent)

    @app.get("/api/sessions/{agent}/{session_id}")
    def history(agent: str, session_id: str):
        entity = service.registry.build(agent)
        memory = service.runner.memory(entity)
        memory.ensure(session_id, agent, "Диалог")
        return memory.load(session_id, include_message_ids=True)

    @app.put('/api/sessions/{agent}/{session_id}/messages/{message_id}/feedback')
    def agent_feedback(agent: str, session_id: str, message_id: int, draft: Feedback):
        entity = service.registry.build(agent)
        return save_response_feedback(service.runner.memory(entity), agent, session_id, message_id, draft)

    def approval_open(item):
        return not item.event.is_set() and time.monotonic() < item.deadline and not item.cancelled.is_set()

    @app.get('/api/confirmations')
    def open_confirmations(request: Request):
        requester = request.state.user['id'] if auth else None
        with pending_lock:
            approvals = [item.details for item in pending.values() if item.owner == requester and approval_open(item)]
        return JSONResponse(approvals, headers={'Cache-Control': 'no-store'})

    @app.post("/api/confirmations/{approval_id}")
    def decide(approval_id: str, decision: Decision, request: Request):
        with pending_lock:
            item = pending.get(approval_id)
            if item is not None and auth and item.owner != request.state.user["id"]:
                raise HTTPException(404,"Confirmation not found")
            if item is None or not approval_open(item):
                raise HTTPException(404, "Confirmation expired or already resolved")
            item.approved = decision.approved
            item.event.set()
        return {"ok": True}

    def find_poll_run(run_id, request):
        with streams_lock:
            item = poll_runs.get(run_id)
        if item is None or item['owner'] != (request.state.user['id'] if auth else None):
            raise HTTPException(404, 'Запуск не найден или истёк')
        return item

    @app.get('/api/chat/runs/{run_id}')
    def poll_chat(run_id: str, request: Request):
        item = find_poll_run(run_id, request)
        out = []
        for _ in range(256):
            try: event = item['events'].get_nowait()
            except queue.Empty: break
            if event is not None: out.append(event)
        done = item['done'].is_set() and item['events'].empty()
        if done:
            with streams_lock: poll_runs.pop(run_id, None)
            if item.get('expiry'): item['expiry'].cancel()
        return JSONResponse({'events': out, 'done': done}, headers={'Cache-Control': 'no-store'})

    @app.post('/api/chat/runs/{run_id}/cancel')
    def cancel_poll_chat(run_id: str, request: Request):
        item = find_poll_run(run_id, request)
        item['cancel'].set()
        return {'ok': True}

    @app.post("/api/chat")
    async def chat(draft: ChatRequest, request: Request):
        if draft.knowledge_collections is not None:
            from superagent.integrations.knowledge import Knowledge
            Knowledge(service.root).validate_collections(draft.knowledge_collections)
        if draft.files:
            from superagent.integrations.office_files import OfficeFiles
            store=OfficeFiles(service.root)
            attached=[store.info(file_id) for file_id in draft.files]
            draft.message+='\n\nПрикреплённые файлы (для чтения используй office_read):\n'+json.dumps([{'file_id':f['id'],'name':f['name']} for f in attached],ensure_ascii=False)
        if draft.agent: service.registry.build(draft.agent)
        elif not draft.conversation: raise ValueError('Выберите агента или обычный чат')
        if draft.conversation and not draft.agent:
            from superagent.core.providers.factory import make_provider
            make_provider(draft.model, service.config)
        from superagent.auth import RevocableSessionEvent
        cancellation = RevocableSessionEvent(auth,request.cookies.get('session')) if auth else threading.Event()
        with streams_lock:
            if len(active_streams) >= 4:
                raise HTTPException(429, "Too many active runs")
            if draft.transport == 'poll' and len(poll_runs) >= 64:
                raise HTTPException(429, 'Слишком много запусков. Повторите позже.')
            active_streams.add(cancellation)
            stream_users[cancellation] = request.state.user["id"] if auth else None
        events = queue.Queue(maxsize=512)
        ids = set()
        run_done = threading.Event()
        run_id = uuid4().hex
        if draft.transport == 'poll':
            with streams_lock:
                poll_runs[run_id] = {'owner': request.state.user['id'] if auth else None, 'events': events, 'cancel': cancellation, 'done': run_done}
            def expire():
                cancellation.set()
                with streams_lock: poll_runs.pop(run_id, None)
            expiry = threading.Timer(900, expire)
            expiry.daemon = True
            with streams_lock: poll_runs[run_id]['expiry'] = expiry
            expiry.start()

        def emit(event):
            while not cancellation.is_set():
                try:
                    events.put(event, timeout=.25)
                    return
                except queue.Full:
                    pass

        def confirm(event):
            with pending_lock:
                item = pending.get(event["id"])
            if item is None:
                return False
            while not cancellation.is_set() and time.monotonic() < item.deadline:
                if item.event.wait(.25):
                    break
            with pending_lock:
                pending.pop(event["id"], None)
            expired = not item.event.is_set()
            approved = not expired and item.approved and not cancellation.is_set()
            emit({'type': 'confirmation_result', 'id': event['id'], 'approved': approved, 'expired': expired})
            return approved

        def worker():
            try:
                if draft.conversation:
                    run = service.runner.run_conversation(draft.message, draft.model, attached_agent=draft.agent, session_id=draft.session_id, confirm=confirm, cancelled=cancellation, images=draft.images, web_enabled=draft.web_enabled, connections=draft.connections,knowledge=draft.knowledge_collections)
                else:
                    run = service.runner.run(draft.agent, draft.message, draft.session_id, confirm, cancelled=cancellation, images=draft.images, web_enabled=draft.web_enabled,knowledge=draft.knowledge_collections)
                with closing(run):
                    for event in run:
                        if cancellation.is_set():
                            break
                        if event['type'] == 'confirmation':
                            deadline = time.monotonic() + service.config.confirmation_timeout
                            with pending_lock:
                                pending[event['id']] = Pending(details=event, deadline=deadline, cancelled=cancellation,
                                                               owner=request.state.user['id'] if auth else None)
                                ids.add(event['id'])
                        emit(event)
            except Exception as exc:
                emit({"type": "error", "error": service.audit.redact(str(exc))})
            finally:
                emit(None)
                run_done.set()
                with pending_lock:
                    for id_ in ids:
                        pending.pop(id_, None)
                with streams_lock:
                    active_streams.discard(cancellation)
                    stream_users.pop(cancellation, None)

        worker_context = contextvars.copy_context()
        if draft.transport == 'poll':
            threading.Thread(target=lambda: worker_context.run(worker), daemon=True).start()
            return JSONResponse({'run_id': run_id}, headers={'Cache-Control': 'no-store'})
        async def stream():
            threading.Thread(target=lambda: worker_context.run(worker), daemon=True).start()
            try:
                while True:
                    if cancellation.is_set() or await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.to_thread(events.get, True, 1)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        break
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            finally:
                cancellation.set()
                with pending_lock:
                    for id_ in ids:
                        item = pending.pop(id_, None)
                        if item:
                            item.event.set()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    return app
