"""An HttpTransport that answers from a script, for cases real servers don't produce."""

import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from agents.mcp.errors import MCPTransportError
from agents.mcp.http import HttpRequest


@dataclass
class Reply:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    async def chunks(self) -> AsyncIterator[bytes]:
        if self.body is not None:
            yield (
                self.body if isinstance(self.body, str) else json.dumps(self.body)
            ).encode()


def result(request: HttpRequest, value: dict[str, Any], **headers: str) -> Reply:
    message = json.loads(request.body or "{}")
    return Reply(
        body={"jsonrpc": "2.0", "id": message.get("id"), "result": value},
        headers={"content-type": "application/json", **headers},
    )


def error(request: HttpRequest, code: int, data: Any = None) -> Reply:
    message = json.loads(request.body or "{}")
    return Reply(
        body={
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {"code": code, "message": "error", "data": data},
        },
        headers={"content-type": "application/json"},
    )


@dataclass
class ScriptedTransport:
    """Calls ``answer(request, method)`` for each request; ``None`` fails it."""

    answer: Callable[[HttpRequest, str | None], Reply | None]
    requests: list[HttpRequest] = field(default_factory=list)

    @property
    def methods(self) -> list[str | None]:
        return [method_of(request) for request in self.requests]

    @asynccontextmanager
    async def request(self, request: HttpRequest) -> AsyncGenerator[Reply]:
        self.requests.append(request)
        reply = self.answer(request, method_of(request))
        if reply is None:
            raise MCPTransportError("connection refused")
        yield reply


def method_of(request: HttpRequest) -> str | None:
    if not request.body:
        return request.method
    return json.loads(request.body).get("method")


MODERN_DISCOVER = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
INITIALIZED = {
    "protocolVersion": "2025-11-25",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "s", "version": "1"},
}
