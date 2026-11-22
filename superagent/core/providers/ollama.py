import copy
import json
from uuid import uuid4

import httpx

from .base import ProviderError, check_response
from .openai import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    def stream(self, messages, tools):
        messages = copy.deepcopy(messages)
        for message in messages:
            message["content"] = message.get("content") or ""
            if isinstance(message["content"], list):
                parts = message["content"]
                message["content"] = "\n".join(p["text"] for p in parts if p["type"] == "text")
                message["images"] = [p["image_url"]["url"].split(",", 1)[1] for p in parts if p["type"] == "image_url"]
            for call in message.get("tool_calls", []):
                if isinstance(call["function"]["arguments"], str):
                    call["function"]["arguments"] = json.loads(call["function"]["arguments"])
        content, calls = "", []
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                body = {"model": self.model, "messages": messages, "stream": True}
                if tools:
                    body["tools"] = tools
                with client.stream("POST", f"{self.base_url}/api/chat", json=body) as response:
                    check_response(response)
                    for line in response.iter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise ProviderError("Ollama reported a streaming error")
                        message = data.get("message", {})
                        if message.get("content"):
                            content += message["content"]
                            yield {"type": "delta", "text": message["content"]}
                        for item in message.get("tool_calls", []):
                            fn = item["function"]
                            calls.append({"id": f"call_{uuid4().hex}", "type": "function", "function": {
                                "name": fn["name"], "arguments": json.dumps(fn["arguments"]) if isinstance(fn["arguments"], dict) else fn["arguments"]}})
        except httpx.HTTPError as exc:
            raise ProviderError("Cannot reach Ollama; check that the server is running and the model is installed") from exc
        if not content and not calls:
            raise ProviderError("Ollama returned an empty response")
        message = {"role": "assistant", "content": content or None}
        if calls:
            message["tool_calls"] = calls
        yield {"type": "message", "message": message}
