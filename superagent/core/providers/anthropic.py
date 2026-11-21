import json
from uuid import uuid4

import httpx

from .base import ProviderError, check_response
from .openai import OpenAIProvider


def anthropic_messages(messages):
    result = []
    for message in messages:
        if message["role"] == "system":
            continue
        if message["role"] == "tool":
            role = "user"
            blocks = [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message["content"]}]
        else:
            role = message["role"]
            blocks = [{"type": "text", "text": message["content"]}] if message.get("content") else []
            for call in message.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": json.loads(call["function"]["arguments"])})
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(blocks)
        else:
            result.append({"role": role, "content": blocks})
    return result


class AnthropicProvider(OpenAIProvider):
    def stream(self, messages, tools):
        if not self.api_key:
            raise ProviderError("Set ANTHROPIC_API_KEY or configure an API key")
        body = {"model": self.model, "max_tokens": 4096, "stream": True,
                "system": "\n".join(m["content"] for m in messages if m["role"] == "system"),
                "messages": anthropic_messages(messages)}
        if tools:
            body["tools"] = [{"name": t["function"]["name"], "description": t["function"]["description"], "input_schema": t["function"]["parameters"]} for t in tools]
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        content, calls = "", {}
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                with client.stream("POST", f"{self.base_url}/v1/messages", json=body, headers=headers) as response:
                    check_response(response)
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = json.loads(line[5:])
                        if data["type"] == "error":
                            raise ProviderError("Anthropic reported a streaming error")
                        if data["type"] == "content_block_start" and data["content_block"]["type"] == "tool_use":
                            block = data["content_block"]
                            calls[data["index"]] = {"id": block.get("id") or f"call_{uuid4().hex}", "type": "function", "function": {"name": block["name"], "arguments": ""}}
                        if data["type"] == "content_block_delta":
                            delta = data["delta"]
                            if delta["type"] == "text_delta":
                                content += delta["text"]
                                yield {"type": "delta", "text": delta["text"]}
                            elif delta["type"] == "input_json_delta":
                                calls[data["index"]]["function"]["arguments"] += delta["partial_json"]
        except httpx.HTTPError as exc:
            raise ProviderError("Cannot reach Anthropic API or request timed out") from exc
        message = {"role": "assistant", "content": content or None}
        if calls:
            for call in calls.values():
                call["function"]["arguments"] = call["function"]["arguments"] or "{}"
            message["tool_calls"] = list(calls.values())
        if not content and not calls:
            raise ProviderError("Anthropic returned an empty response")
        yield {"type": "message", "message": message}
