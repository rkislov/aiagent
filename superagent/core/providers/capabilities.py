"""Exclude obvious non-chat model families from chat pickers, keep discovery intact."""
from urllib.parse import urlsplit

RETIRED_IMAGE_MODELS={'gpt-image-1','dall-e-2','dall-e-3'}


def image_models(provider):
    return [m for m in provider.models if ('gpt-image' in m or m.startswith('chatgpt-image')) and (urlsplit(provider.base_url).hostname!='api.openai.com' or m not in RETIRED_IMAGE_MODELS)]


def supports_chat(provider,model):
    if provider.type!='openai' or urlsplit(provider.base_url).hostname!='api.openai.com':return True
    name=model.lower()
    return not (any(word in name for word in ('embedding','image','dall-e','whisper','tts','moderation','realtime','transcribe','audio','instruct','codex','deep-research')) or name.startswith(('babbage','davinci')))
