from pydantic import SecretStr
from .gateway.config import Config, credential_entries
from .gateway.registry import atomic_write
import yaml


def unpack(value):
    if isinstance(value, SecretStr): return value.get_secret_value()
    if isinstance(value, dict): return {k: unpack(v) for k,v in value.items()}
    if isinstance(value, list): return [unpack(v) for v in value]
    return value


def public(config):
    data = config.model_dump()
    for entry in [*data['providers'].values(), *data['connections'].values(), *data['channels'].values()]:
        for key in ('api_key', 'password', 'secret'):
            if key in entry:
                entry[key + '_present'] = bool(entry.pop(key))
    return data


def update(service, draft):
    previous = unpack(service.config.model_dump())
    for section in ('providers', 'connections', 'channels'):
        for name, entry in draft.get(section, {}).items():
            old = previous[section].get(name, {})
            for key in ('api_key', 'password', 'secret'):
                entry.pop(key + '_present', None)
                if key in old and not entry.get(key): entry[key] = old[key]
    candidate = Config.model_validate({**previous, **draft})
    if (service.root / '.tenant').exists():
        if any(getattr(entry,field,None) for entry in credential_entries(candidate) for field in ('api_key_env','password_env','secret_env')):
            raise ValueError('В пользовательском пространстве используйте собственные ключи, а не окружение сервера')
    atomic_write(service.root / 'config.yaml', yaml.safe_dump(unpack(candidate.model_dump()), allow_unicode=True, sort_keys=False))
    service.config = candidate
    service.registry.config = candidate
    service.runner.builtins.config = candidate
    service.audit.secrets.extend(entry.key() for entry in credential_entries(candidate) if entry.key())
    return public(candidate)
