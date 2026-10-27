"""An `HttpTransport` that serves requests from an ASGI app, in-process.

The client's contract tests point it at the MCP SDK's own servers
(`MCPServer.streamable_http_app()`), so they exercise real wire behaviour
with no network. Leaving a response early disconnects the app, as an
aborted fetch would.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from agents.mcp.http import HttpRequest


@dataclass
class AsgiResponse:
    status: int
    headers: dict[str, str]
    _body: asyncio.Queue[bytes | None]

    async def chunks(self) -> AsyncIterator[bytes]:
        while (chunk := await self._body.get()) is not None:
            yield chunk


@dataclass
class AsgiTransport:
    app: Callable[..., Any]
    requests: list[HttpRequest] = field(default_factory=list)
    disconnects: int = 0
    """How many requests ended with the app still sending."""

    @asynccontextmanager
    async def request(self, request: HttpRequest) -> AsyncGenerator[AsgiResponse]:
        self.requests.append(request)
        url = urlsplit(request.url)
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "scheme": url.scheme,
            "path": url.path or "/",
            "raw_path": (url.path or "/").encode(),
            "query_string": url.query.encode(),
            "root_path": "",
            "headers": [
                (name.lower().encode(), value.encode())
                for name, value in {"host": url.netloc, **request.headers}.items()
            ],
            "server": (url.hostname, url.port or 80),
            "client": ("127.0.0.1", 1234),
        }
        body = (request.body or "").encode()
        sent_body = False
        disconnected = asyncio.Event()
        started: asyncio.Future[tuple[int, dict[str, str]]] = (
            asyncio.get_running_loop().create_future()
        )
        chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        finished = False

        async def receive() -> dict[str, Any]:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            nonlocal finished
            if message["type"] == "http.response.start":
                headers = {
                    name.decode().lower(): value.decode()
                    for name, value in message.get("headers", [])
                }
                started.set_result((message["status"], headers))
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    await chunks.put(message["body"])
                if not message.get("more_body", False):
                    finished = True
                    await chunks.put(None)

        async def run() -> None:
            try:
                await self.app(scope, receive, send)
            finally:
                if not started.done():
                    started.set_exception(RuntimeError("app sent no response"))
                await chunks.put(None)

        task = asyncio.create_task(run())
        try:
            status, headers = await started
            yield AsgiResponse(status=status, headers=headers, _body=chunks)
        finally:
            if not finished:
                self.disconnects += 1
            disconnected.set()
            if not task.done():
                await asyncio.wait({task}, timeout=1)
                task.cancel()
