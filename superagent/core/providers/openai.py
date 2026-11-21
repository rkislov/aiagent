from __future__ import annotations

import json
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .base import ProviderError, check_response


class OpenAIProvider:
    def __init__(self, model: str, base_url: str, api_key: str = "", timeout: float = 120):
        self.model, self.base_url, self.api_key, self.timeout = model, base_url.rstrip("/"), api_key, timeout

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        return next(event["message"] for event in self.stream(messages, tools) if event["type"] == "message")

    def stream(self, messages: list[dict], tools: list[dict]):
        body = {"model": self.model, "messages": messages, "stream": True}
        if urlsplit(self.base_url).hostname == 'api.deepseek.com':
            # Tool turns use the non-thinking mode; no hidden reasoning needs replaying.
            body['thinking'] = {'type': 'disabled'}
        if tools:
            body["tools"] = tools
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        content, calls = "", {}
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                with client.stream("POST", f"{self.base_url}/chat/completions", json=body, headers=headers) as response:
                    check_response(response)
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            break
                        data = json.loads(raw)
                        if "error" in data:
                            raise ProviderError("Provider reported a streaming error")
                        for choice in data.get("choices", [])[:1]:
                            delta = choice.get("delta", {})
                            if delta.get("content"):
                                content += delta["content"]
                                yield {"type": "delta", "text": delta["content"]}
                            for part in delta.get("tool_calls", []):
                                call = calls.setdefault(part["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                                call["id"] += part.get("id", "")
                                for field in ("name", "arguments"):
                                    call["function"][field] += part.get("function", {}).get(field, "")
        except httpx.HTTPError as exc:
            raise ProviderError("Could not reach model API or streaming request timed out") from exc
        for call in calls.values():
            call["id"] = call["id"] or f"call_{uuid4().hex}"
        message = {"role": "assistant", "content": content or None}
        if calls:
            message["tool_calls"] = [calls[key] for key in sorted(calls)]
        if not content and not calls:
            raise ProviderError("Model returned an empty response")
        yield {"type": "message", "message": message}
