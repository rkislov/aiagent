"""Shared model transports; secrets never enter editable tenant settings."""


def resolve_provider(config,name):
    local=config.providers.get(name)
    shared=config._model_pool.get(name)
    if local and local.key():return local
    if shared and (local is None or (local.type==shared.type and local.base_url.rstrip('/')==shared.base_url.rstrip('/'))):return shared
    return local


def effective_providers(config):
    return {name:resolve_provider(config,name) for name in dict.fromkeys([*config.providers,*config._model_pool])}
