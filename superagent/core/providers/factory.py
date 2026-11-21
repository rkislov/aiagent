from superagent.gateway.config import Config
from .anthropic import AnthropicProvider
from .base import ProviderError
from .demo import DemoProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider


def make_provider(model: str, config: Config):
    name, model_id = model.split(":", 1)
    if name not in config.providers:
        raise ProviderError(f"Unknown provider: {name}")
    provider = config.providers[name]
    if provider.type == "demo":
        return DemoProvider()
    classes = {"openai": OpenAIProvider, "anthropic": AnthropicProvider, "ollama": OllamaProvider}
    if provider.type not in classes:
        raise ProviderError(f"Unsupported provider type: {provider.type}")
    return classes[provider.type](model_id, provider.base_url, provider.key(), config.model_timeout)
