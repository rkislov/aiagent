from superagent.gateway.config import Config
from .anthropic import AnthropicProvider
from .base import ProviderError
from .demo import DemoProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider


def make_provider(model: str, config: Config):
    from .pool import resolve_provider
    name, model_id = model.split(":", 1)
    provider=resolve_provider(config,name)
    if provider is None:
        raise ProviderError(f"Unknown provider: {name}")
    if provider is config._model_pool.get(name) and model_id not in provider.models:
        raise ProviderError('Модель не разрешена в общем каталоге администратора')
    if provider.type == "demo":
        return DemoProvider()
    classes = {"openai": OpenAIProvider, "anthropic": AnthropicProvider, "ollama": OllamaProvider}
    if provider.type not in classes:
        raise ProviderError(f"Unsupported provider type: {provider.type}")
    return classes[provider.type](model_id, provider.base_url, provider.key(), config.model_timeout)
