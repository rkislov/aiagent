"""Persist a discovered model list without writing other form fields."""
import hashlib
from .config import ProviderConfig
from superagent.settings import update


def snapshot(config, name):
    provider = config.providers.get(name)
    return hashlib.sha256((provider.model_dump_json()+'\0'+provider.key()).encode()).hexdigest() if provider else None


def save_models(service, name, base, models, expected):
    if snapshot(service.config, name) != expected:
        raise ValueError('Настройки подключения изменились. Обновите список моделей ещё раз.')
    stored = service.config.providers.get(name)
    if stored and (stored.type != 'openai' or stored.base_url.rstrip('/') != base.rstrip('/')):
        raise ValueError('Сначала сохраните новый адрес подключения, затем обновите модели.')
    candidate = service.config.model_copy(deep=True)
    if stored:
        candidate.providers[name].models = models
    else:
        candidate.providers[name] = ProviderConfig(type='openai', base_url=base, api_key_env=None, models=models)
    # update receives server state only, never the browser's unfinished settings.
    update(service, candidate.model_dump())
