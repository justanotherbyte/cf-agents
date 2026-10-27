"""Serving one Workers request with an ASGI app.

The app runs in its own task. A response sent in one body message is
returned whole; one sent in several (SSE) streams as the client reads it.
"""

import asyncio
import codecs
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any
from urllib.parse import unquote, urlsplit

from starlette.types import Message, Receive, Scope, Send
from workers import Request, Response

from ... import _ffi

__all__ = ("serve",)

# The app tasks still running; asyncio itself keeps only weak references.
_running: set[asyncio.Task[None]] = set()


async def serve(
    app: Callable[[Scope, Receive, Send], Coroutine[Any, Any, None]], request: Request
) -> Response:
    """Run ``app`` for ``request`` and return its response.

    Returns once the app sends its first body message, so a streamed body
    keeps being produced while the client reads it.

    Raises
    ------
    Exception
        Whatever the app raised before it started responding.
    RuntimeError
        If the app returned without responding.
    """
    exchange = _Exchange(await request.bytes())
    task = asyncio.create_task(app(_scope(request), exchange.receive, exchange.send))
    _running.add(task)
    task.add_done_callback(_running.discard)

    started = asyncio.create_task(exchange.started.wait())
    await asyncio.wait((task, started), return_when=asyncio.FIRST_COMPLETED)
    if not started.done():
        started.cancel()
        task.result()
        raise RuntimeError("The MCP server returned without responding")

    if exchange.finished.is_set():
        await task
        return Response(
            exchange.body(), status=exchange.status, headers=exchange.headers
        )
    # shortcut: streaming_response always answers 200; every SDK stream (SSE)
    # is a 200, so pass the status through if that ever changes.
    return _ffi.streaming_response(exchange.stream(task), headers=exchange.headers)


def _scope(request: Request) -> Scope:
    url = urlsplit(request.url)
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": str(request.method),
        "scheme": url.scheme,
        "path": unquote(url.path),
        "raw_path": url.path.encode(),
        "query_string": url.query.encode(),
        "root_path": "",
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in request.headers.items()
        ],
    }


class _Exchange:
    """The ASGI ``receive`` / ``send`` pair for one request."""

    __slots__ = ("_chunks", "_request_body", "finished", "headers", "started", "status")

    def __init__(self, body: bytes) -> None:
        self._request_body: bytes | None = body
        self._chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.status = 200
        self.headers: dict[str, str] = {}
        self.started = asyncio.Event()  # the first body message was sent
        self.finished = asyncio.Event()  # the last one was, or the client left

    async def receive(self) -> Message:
        if self._request_body is not None:
            body, self._request_body = self._request_body, None
            return {"type": "http.request", "body": body, "more_body": False}
        await self.finished.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.headers = {
                name.decode("latin-1"): value.decode("latin-1")
                for name, value in message.get("headers", ())
            }
            return
        if message["type"] != "http.response.body":
            return

        self._chunks.put_nowait(message.get("body", b""))
        if not message.get("more_body", False):
            self._chunks.put_nowait(None)
            self.finished.set()
        self.started.set()

    def body(self) -> bytes:
        """Return a finished response's body."""
        chunks: list[bytes] = []
        while (chunk := self._chunks.get_nowait()) is not None:
            chunks.append(chunk)
        return b"".join(chunks)

    async def stream(self, task: asyncio.Task[None]) -> AsyncIterator[str]:
        """Yield the body as it's sent; stop the app if the client goes away."""
        decoder = codecs.getincrementaldecoder("utf-8")()
        # An app that fails mid-stream never sends the last chunk; end the
        # stream anyway so `await task` below raises its error.
        task.add_done_callback(lambda _: self._chunks.put_nowait(None))
        try:
            while (chunk := await self._chunks.get()) is not None:
                yield decoder.decode(chunk)
            await task
        finally:
            self.finished.set()
            task.cancel()
