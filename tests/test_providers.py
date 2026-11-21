import json

import httpx
import pytest

from superagent.core.providers.anthropic import AnthropicProvider, anthropic_messages
from superagent.core.providers.base import ProviderError
from superagent.core.providers.ollama import OllamaProvider
from superagent.core.providers.openai import OpenAIProvider


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def sse(items):
    return "\n\n".join("data: " + json.dumps(item) for item in items) + "\n\ndata: [DONE]\n\n"


def test_openai_streaming_tool_fragments(monkeypatch):
    def handle(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer private"
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, text=sse([
            {"choices": [{"delta": {"content": "Привет"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_a", "function": {"name": "echo", "arguments": '{"text":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"hello"}'}}]}}]},
        ]))
    transport(monkeypatch, handle)
    events = list(OpenAIProvider("model", "https://example.test/v1", "private").stream([], []))
    assert events[0] == {"type": "delta", "text": "Привет"}
    assert json.loads(events[-1]["message"]["tool_calls"][0]["function"]["arguments"]) == {"text": "hello"}


def test_anthropic_translation_and_stream(monkeypatch):
    messages = [{"role": "system", "content": "instructions"}, {"role": "user", "content": "hello"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "t", "function": {"name": "echo", "arguments": '{"text":"ok"}'}}]},
                {"role": "tool", "tool_call_id": "t", "content": '{"result":"ok"}'}]
    assert anthropic_messages(messages)[-1]["content"][0]["type"] == "tool_result"
    def handle(request):
        body = json.loads(request.content)
        assert body["system"] == "instructions"
        assert request.headers["x-api-key"] == "private"
        return httpx.Response(200, text='data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Ready"}}\n\ndata: {"type":"message_stop"}\n\n')
    transport(monkeypatch, handle)
    assert AnthropicProvider("model", "https://example.test", "private").chat(messages, [])["content"] == "Ready"


def test_ollama_tool_arguments(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, text=json.dumps({"message": {"tool_calls": [{"function": {"name": "echo", "arguments": {"text": "hello"}}}]}, "done": True}) + "\n"))
    result = OllamaProvider("model", "http://localhost:11434").chat([], [])
    assert json.loads(result["tool_calls"][0]["function"]["arguments"]) == {"text": "hello"}


def test_remote_errors_do_not_echo_secrets(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(401, text="your private key is private"))
    with pytest.raises(ProviderError) as error:
        OpenAIProvider("model", "https://example.test", "private").chat([], [])
    assert "private" not in str(error.value)


def test_deepseek_uses_own_endpoint_and_key(monkeypatch):
    from superagent.gateway.config import Config, ProviderConfig
    from superagent.core.providers.factory import make_provider
    config=Config()
    config.providers['deepseek']=ProviderConfig(base_url='https://api.deepseek.com',api_key_env=None,api_key='deepseek-only',models=['deepseek-flash'])
    def handler(request):
        assert str(request.url)=='https://api.deepseek.com/chat/completions'
        assert request.headers['authorization']=='Bearer deepseek-only'
        body=json.loads(request.content)
        assert body['thinking']=={'type':'disabled'}
        assert body['model']=='deepseek-flash'
        assert body['tools'][0]['function']['name']=='echo'
        return httpx.Response(200,text=sse([{'choices':[{'delta':{'content':'OK'}}]}]))
    transport(monkeypatch,handler)
    model=make_provider('deepseek:deepseek-flash',config)
    assert model.chat([{'role':'user','content':'hello'}],[{'type':'function','function':{'name':'echo','parameters':{'type':'object'}}}])['content']=='OK'


def test_provider_presets_are_secret_free(platform):
    from fastapi.testclient import TestClient
    from superagent.web.app import create_app
    with TestClient(create_app(platform,authentication=False),base_url='http://localhost') as client:
        presets=client.get('/api/provider-presets').json()
        assert presets['openai']['config']['base_url']=='https://api.openai.com/v1'
        assert presets['deepseek']['config']['base_url']=='https://api.deepseek.com'
        for preset in presets.values():
            assert not preset['config']['api_key']
            assert preset['config']['api_key_env'] is None
