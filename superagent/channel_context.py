"""Persistent remote identities; only an explicit owner binding may use owner tools."""
import hashlib
import json
import sqlite3
import threading
from uuid import uuid4
from pydantic import SecretStr
from .gateway.config import Config

_guard = threading.RLock()


def identity_key(name, cfg, user_id):
    return hashlib.sha256(json.dumps([cfg.type, cfg.url, name, user_id]).encode()).hexdigest()


def user_service(owner, name, cfg, user_id):
    if cfg.owner_user_id and user_id == cfg.owner_user_id:
        return owner
    if cfg.agent:
        raise PermissionError('Другим пользователям доступен только обычный чат')
    key = identity_key(name, cfg, user_id)
    with _guard:
        services = getattr(owner, '_channel_users', None)
        if services is None:
            services = owner._channel_users = {}
        if key not in services:
            from .service import Platform
            root = owner.root / 'data/channel-users' / key
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if root.resolve() != root: raise ValueError('Invalid channel workspace')
            (root / '.tenant').touch(mode=0o600)
            for folder in ('agents', 'skills'): (root / folder).mkdir(exist_ok=True)
            provider_name = cfg.model.split(':', 1)[0]
            from .core.providers.pool import resolve_provider
            provider = resolve_provider(owner.config,provider_name).model_copy(deep=True)
            provider.api_key = SecretStr(provider.key())
            provider.api_key_env = None
            config = Config(providers={provider_name: provider}, model_timeout=owner.config.model_timeout,
                            confirmation_timeout=owner.config.confirmation_timeout)
            config._model_pool=owner.config._model_pool.copy()
            # No owner connections, including search credentials/indexes, are inherited.
            services[key] = Platform(root, config=config)
        # Rotate only the selected model transport when owner settings change.
        provider_name = cfg.model.split(':', 1)[0]
        from .core.providers.pool import resolve_provider
        provider = resolve_provider(owner.config,provider_name).model_copy(deep=True)
        provider.api_key = SecretStr(provider.key()); provider.api_key_env = None
        services[key].config.providers = {provider_name: provider}
        services[key].config._model_pool=owner.config._model_pool.copy()
        if provider.key(): services[key].audit.secrets.append(provider.key())
        return services[key]


class Dialogs:
    def __init__(self, service, identity):
        self.service, self.identity = service, identity
        self.path = service.root / 'data/channel-dialogs.db'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS identities(id TEXT PRIMARY KEY, session TEXT NOT NULL)')
        self.path.chmod(0o600)

    def connect(self): return sqlite3.connect(self.path, timeout=15)

    def current(self, initial=None):
        with _guard, self.connect() as db:
            db.execute('INSERT OR IGNORE INTO identities VALUES(?,?)', (self.identity, initial or uuid4().hex))
            return db.execute('SELECT session FROM identities WHERE id=?', (self.identity,)).fetchone()[0]

    def new(self):
        session = uuid4().hex
        self.service.runner.conversation_memory().ensure(session, '__conversation__', 'Новый диалог бота')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO identities VALUES(?,?)', (self.identity, session))
        return session

    def use(self, session):
        memory = self.service.runner.conversation_memory()
        with memory.connect() as db:
            if not db.execute('SELECT 1 FROM sessions WHERE id=? AND agent=?', (session, '__conversation__')).fetchone():
                raise ValueError('Свой диалог с таким ID не найден')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO identities VALUES(?,?)', (self.identity, session))
        return session

    def list(self, query=''):
        return [row for row in self.service.runner.conversation_memory().sessions('__conversation__')
                if query.casefold() in row['title'].casefold()][:20]
