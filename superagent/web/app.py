from __future__ import annotations

import asyncio
import contextvars
import json
import queue
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from superagent.gateway.models import Agent, StrictModel
from superagent.gateway.registry import parse_skill
from superagent.service import Platform


class SkillDraft(StrictModel):
    name: str
    metadata: str = Field(max_length=100_000)
    code: str = Field(max_length=1_000_000)


class SkillTest(SkillDraft):
    input: dict = Field(default_factory=dict)
    confirmed: bool = False


class ChatRequest(StrictModel):
    agent: str
    message: str = Field(min_length=1, max_length=100_000)
    session_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")


class Decision(StrictModel):
    approved: bool


@dataclass
class Pending:
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

    def revoke_streams(uid):
        with streams_lock:
            for cancel, owner_id in list(stream_users.items()):
                if owner_id == uid: cancel.set()
    static = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app):
        yield
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

    app = FastAPI(title="Superagent", lifespan=lifespan)
    app.state.platform = owner
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin != expected:
            return JSONResponse({"detail": "Cross-origin requests are forbidden"}, status_code=403)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if not request.headers.get("content-type", "").startswith("application/json"):
                return JSONResponse({"detail": "application/json required"}, status_code=415)
            length = request.headers.get("content-length")
            if length and int(length) > 2_000_000:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
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
        if request.url.path.startswith("/api/auth/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc) if auth else service.audit.redact(str(exc))}, status_code=400)

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
        if not request.state.user['admin']: raise HTTPException(403,'Только администратор')
        uid = auth.reset_two_factor(request.state.user['id'],draft.get('username',''),draft.get('password',''),draft.get('code',''))
        revoke_streams(uid)
        return {'ok':True}

    @app.post('/api/users')
    def create_user(request: Request, draft: dict):
        if not request.state.user['admin']: raise HTTPException(403,'Только администратор')
        return auth.create(draft.get('username',''),draft.get('password',''))

    @app.get("/api/settings")
    def settings():
        from superagent.settings import public
        return public(service.config)

    @app.get('/api/provider-presets')
    def provider_presets():
        from superagent.gateway.provider_presets import PRESETS
        return PRESETS

    @app.put("/api/settings")
    def save_settings(draft: dict):
        from superagent.settings import update
        with registry_lock:
            return update(service, draft)

    @app.post("/api/providers/test")
    def test_provider(draft: dict):
        from superagent.core.providers.factory import make_provider
        model = make_provider(draft.get("model", "demo:office"), service.config)
        try:
            result = model.chat([{"role": "user", "content": "Reply OK"}], [])
            return {"ok": True, "response": result.get("content")}
        except Exception:
            raise ValueError("Не удалось подключиться к модели. Проверьте адрес, ключ и имя модели") from None

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
        models = list_models(base, key)
        ticket = uuid4().hex
        with registry_lock:
            now = time.monotonic()
            for old, value in list(model_discoveries.items()):
                if value['expires'] < now: model_discoveries.pop(old,None)
            if len(model_discoveries) >= 128:
                model_discoveries.pop(next(iter(model_discoveries)))
            model_discoveries[ticket] = {'root':str(service.root),'name':name,'base':base,'models':models,'expected':expected,'generation':generation,'expires':now+120}
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
            save_models(service,result['name'],result['base'],result['models'],result['expected'])
        return JSONResponse({'saved':True,'models':result['models']}, headers={'Cache-Control':'no-store'})

    @app.post("/api/connections/{name}/test")
    def connection_test(name: str, draft: dict):
        from superagent.integrations.catalog import BUILTINS
        if name not in BUILTINS:
            raise ValueError("Неизвестное подключение")
        from superagent.integrations.task_writes import WRITE_TOOLS
        if name in WRITE_TOOLS: raise ValueError("Запись требует предпросмотра и подтверждения в чате или CLI")
        if name == "ssh_run": raise ValueError("Выполняйте SSH через агента с подтверждением команды")
        result = service.runner.builtins.run(name, draft)
        service.audit.record(agent="__test__", kind="skill", skill=name, input=draft, output=result)
        return result

    @app.get('/api/agent-templates/work_manager')
    def work_manager_template():
        from superagent.gateway.agent_templates import work_manager
        models=[f'{name}:{model}' for name,cfg in service.config.providers.items() if cfg.type!='demo' and (cfg.key() or cfg.type=='ollama') for model in cfg.models]
        return work_manager(models[0] if models else 'openai:gpt-4o-mini').model_dump()

    @app.get("/api/workspace")
    def workspace():
        with registry_lock:
            service.registry.scan()
            return {"agents": [a.model_dump() for a in service.registry.agents.values()],
                    "skills": [s.model_dump() for s in service.registry.skills.values()],
                    "errors": service.registry.errors,
                    "builtin_skills": list(__import__("superagent.integrations.catalog", fromlist=["BUILTINS"]).BUILTINS),
                    "models": [f"{name}:{m}" for name, p in service.config.providers.items() for m in p.models],
                    "providers": [{"name": name, "type": p.type, "configured": bool(p.key()) or p.type in ("demo", "ollama")} for name, p in service.config.providers.items()]}

    @app.put("/api/agents/{name}")
    def save_agent(name: str, agent: Agent):
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

    @app.get("/api/sessions/{agent}")
    def sessions(agent: str):
        entity = service.registry.build(agent)
        return service.runner.memory(entity).sessions(agent)

    @app.get("/api/sessions/{agent}/{session_id}")
    def history(agent: str, session_id: str):
        entity = service.registry.build(agent)
        memory = service.runner.memory(entity)
        memory.ensure(session_id, agent, "Диалог")
        return memory.load(session_id)

    @app.post("/api/confirmations/{approval_id}")
    def decide(approval_id: str, decision: Decision, request: Request):
        with pending_lock:
            item = pending.get(approval_id)
            if item is not None and auth and item.owner != request.state.user["id"]:
                raise HTTPException(404,"Confirmation not found")
            if item is None or item.event.is_set() or item.cancelled.is_set():
                raise HTTPException(404, "Confirmation expired or already resolved")
            item.approved = decision.approved
            item.event.set()
        return {"ok": True}

    @app.post("/api/chat")
    async def chat(draft: ChatRequest, request: Request):
        service.registry.build(draft.agent)
        from superagent.auth import RevocableSessionEvent
        cancellation = RevocableSessionEvent(auth,request.cookies.get('session')) if auth else threading.Event()
        with streams_lock:
            if len(active_streams) >= 4:
                raise HTTPException(429, "Too many active runs")
            active_streams.add(cancellation)
            stream_users[cancellation] = request.state.user["id"] if auth else None
        events = queue.Queue(maxsize=512)
        ids = set()

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
            deadline = time.monotonic() + service.config.confirmation_timeout
            while not cancellation.is_set() and time.monotonic() < deadline:
                if item.event.wait(.25):
                    break
            with pending_lock:
                pending.pop(event["id"], None)
            return item.event.is_set() and item.approved and not cancellation.is_set()

        def worker():
            try:
                for event in service.runner.run(draft.agent, draft.message, draft.session_id, confirm, cancelled=cancellation):
                    if cancellation.is_set():
                        break
                    if event["type"] == "confirmation":
                        with pending_lock:
                            pending[event["id"]] = Pending(cancelled=cancellation, owner=request.state.user["id"] if auth else None)
                            ids.add(event["id"])
                    emit(event)
            except Exception as exc:
                emit({"type": "error", "error": service.audit.redact(str(exc))})
            finally:
                emit(None)
                with pending_lock:
                    for id_ in ids:
                        pending.pop(id_, None)
                with streams_lock:
                    active_streams.discard(cancellation)
                    stream_users.pop(cancellation, None)

        worker_context = contextvars.copy_context()
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
