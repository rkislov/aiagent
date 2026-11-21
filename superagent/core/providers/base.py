from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

import httpx


class ModelProvider(Protocol):
    def chat(self, messages: list[dict], tools: list[dict]) -> dict: ...
    def stream(self, messages: list[dict], tools: list[dict]) -> Iterator[dict]: ...


class ProviderError(RuntimeError):
    pass


def check_response(response: httpx.Response):
    if response.is_error:
        # Do not echo remote bodies, request headers, URLs with credentials, or keys.
        raise ProviderError(f"Model API returned HTTP {response.status_code}; check model, endpoint and credentials")
