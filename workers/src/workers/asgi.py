"""Serve an ASGI application, such as FastAPI, from a Worker.

Ported from the stock SDK at cb2db3c. Background work after a streamed
response or a WebSocket upgrade goes through `ctx.waitUntil` when the caller
passes `ctx`, because the module-level waitUntil doesn't keep a Python Worker
alive past the response.
"""

import logging
from asyncio import Event, Future, Queue, create_task, ensure_future
from collections.abc import Awaitable
from contextlib import contextmanager, suppress
from typing import Any
from urllib.parse import unquote

import js

from ._adapter import NULL_BODY_STATUSES, body_encoding, request_headers
from ._js import to_js_headers as _to_js_headers
from .context import ExecutionContext, js_promise
from .entrypoints import WorkerEntrypoint
from .request import Request
from .types import Context

ASGI = {"spec_version": "2.0", "version": "3.0"}
logger = logging.getLogger("asgi")
background_tasks = set()


def run_in_background(coro: Awaitable[Any]) -> Future:
    fut = ensure_future(coro)
    background_tasks.add(fut)

    def _on_done(f):
        background_tasks.discard(f)
        exc = f.exception() if not f.cancelled() else None
        if exc is not None:
            logger.error("Unhandled exception in background task", exc_info=exc)

    fut.add_done_callback(_on_done)
    return fut


async def close_stream_quietly(writer):
    try:
        await writer.close()
    except Exception:
        logger.debug("Closing the response stream failed", exc_info=True)


@contextmanager
def acquire_js_buffer(pybuffer):
    from pyodide.ffi import create_proxy

    px = create_proxy(pybuffer)
    buf = px.getBuffer()
    px.destroy()

    try:
        yield buf.data
    finally:
        buf.release()


def request_to_scope(req, env, ws=False, state=None):
    from js import URL

    # @app.get("/example")
    # async def example(request: Request):
    #     request.headers.get("content-type")
    # - this will error if header is not "bytes" as in ASGI spec.
    headers = [(k.lower().encode(), v.encode()) for k, v in request_headers(req)]

    url = URL.new(req.url)
    assert url.protocol[-1] == ":"
    assert "?".startswith(url.search[0:1])

    scope = {
        "asgi": ASGI,
        "headers": headers,
        "http_version": "1.1",
        "method": req.method,
        "scheme": url.protocol[:-1],
        # ASGI requires "path" to be percent-decoded; the encoded original is
        # available to apps via "raw_path".
        # https://asgi.readthedocs.io/en/latest/specs/www.html#http-connection-scope
        "path": unquote(url.pathname),
        "raw_path": url.pathname.encode(),
        "query_string": url.search[1:].encode(),
        "type": "websocket" if ws else "http",
        "env": env,
    }
    if state is not None:
        # ASGI requires a shallow copy of lifespan state for each request.
        scope["state"] = dict(state)

    return scope


def _resolve(future: Future, value: Any) -> None:
    if not future.done():
        future.set_result(value)


def _fail(future: Future, message: str) -> None:
    if not future.done():
        future.set_exception(RuntimeError(message))


class _Lifespan:
    """One ASGI lifespan startup and shutdown cycle of an app.

    `startup` resolves True on `lifespan.startup.complete`, False when the app
    has no lifespan support, and raises on `lifespan.startup.failed`.
    `shutdown_complete` mirrors the shutdown phase.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self.state: dict[str, Any] = {}
        self.startup: Future[bool] = Future()
        self.shutdown_complete: Future[None] = Future()
        self.receive_queue: Queue[dict[str, Any]] = Queue()
        self.receive_queue.put_nowait({"type": "lifespan.startup"})

    async def receive(self) -> dict[str, Any]:
        return await self.receive_queue.get()

    async def send(self, got: dict[str, Any]) -> None:
        # Failures use `or`, not a .get default: an empty message field is
        # legal and would otherwise produce RuntimeError('').
        message = got.get("message")
        if got["type"] == "lifespan.startup.complete":
            _resolve(self.startup, True)
        elif got["type"] == "lifespan.startup.failed":
            _fail(self.startup, message or "ASGI lifespan startup failed")
        elif got["type"] == "lifespan.shutdown.complete":
            _resolve(self.shutdown_complete, None)
        elif got["type"] == "lifespan.shutdown.failed":
            _fail(self.shutdown_complete, message or "ASGI lifespan shutdown failed")
        else:
            raise RuntimeError(f"Unexpected lifespan event {got['type']}")

    async def run(self) -> None:
        scope = {"asgi": ASGI, "state": self.state, "type": "lifespan"}
        try:
            await self.app(scope, self.receive, self.send)
        except Exception as exc:
            # Spec: an exception raised before startup is acked signals that
            # the app doesn't support lifespan; serve requests anyway. Log it,
            # or an app that crashed mid-startup looks like one with no
            # lifespan handler at all.
            if not self.startup.done():
                logger.debug(
                    "ASGI lifespan task raised before reporting startup; "
                    "treating the app as not supporting lifespan",
                    exc_info=exc,
                )
                self.startup.set_result(False)
                return

            # After a successful startup, a shutdown-phase error can't affect
            # the already-served request, so log it and let shutdown complete.
            logger.exception("Exception in ASGI lifespan application", exc_info=exc)
            _resolve(self.shutdown_complete, None)
            return

        # App returned without acking: a missing startup ack means it has no
        # lifespan handler, so mark startup unsupported rather than hanging.
        if not self.startup.done():
            self.startup.set_result(False)
        else:
            _resolve(self.shutdown_complete, None)

    async def shutdown(self) -> None:
        # Nothing to shut down if the app never completed a lifespan startup.
        if self.startup.done() and not self.startup.result():
            return

        await self.receive_queue.put({"type": "lifespan.shutdown"})
        await self.shutdown_complete


async def _no_lifespan_shutdown() -> None:
    return


async def start_application(app):
    # Drives one ASGI lifespan startup/shutdown cycle before/after serving the
    # request. The lifespan protocol is optional, so we must tolerate apps that
    # don't implement it and fall back to serving requests without lifespan.
    # https://asgi.readthedocs.io/en/latest/specs/lifespan.html
    lifespan = _Lifespan(app)
    run_in_background(lifespan.run())
    if not await lifespan.startup:
        return _no_lifespan_shutdown, lifespan.state

    return lifespan.shutdown, lifespan.state


class _HttpCycle:
    """One HTTP request through an ASGI app, and the `js.Response` it sends."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.status: int | None = None
        self.headers: list[tuple[str, str]] | None = None
        self.result: Future[js.Response] = Future()
        self.finished_response = Event()
        self.receive_queue: Queue[dict[str, Any]] = Queue()
        # Streaming state, set on the first body chunk with more_body=True.
        self.writer: Any = None
        self.writer_closed = False

    async def queue_body(self, req: "Request | js.Request") -> None:
        if req.body:
            async for data in req.body:
                await self.receive_queue.put(
                    {"body": data.to_bytes(), "more_body": True, "type": "http.request"}
                )
        await self.receive_queue.put(
            {"body": b"", "more_body": False, "type": "http.request"}
        )

    async def receive(self) -> dict[str, Any]:
        if not self.receive_queue.empty():
            return await self.receive_queue.get()

        await self.finished_response.wait()
        return {"type": "http.disconnect"}

    async def send(self, got: dict[str, Any]) -> None:
        if got["type"] == "http.response.start":
            self.status = got["status"]
            # Like above, we need to convert byte-pairs into string explicitly.
            # A response header name may repeat (multiple Set-Cookie), so this
            # must stay a sequence of pairs, not a name-keyed mapping.
            self.headers = [(k.decode(), v.decode()) for k, v in got.get("headers", [])]
        elif got["type"] == "http.response.body":
            await self._send_body(got.get("body", b""), got.get("more_body", False))

    async def _send_body(self, body: bytes, more_body: bool) -> None:
        if self.writer is None and more_body:
            self._start_stream()
        if self.writer is None:
            self._respond(body)
            return

        with acquire_js_buffer(body) as jsbytes:
            await self.writer.write(jsbytes.slice())
        if not more_body:
            await self.writer.close()
            self.writer_closed = True
            self.finished_response.set()

    def _start_stream(self) -> None:
        # A TransformStream lets the runtime consume body chunks as they are
        # written.
        from js import Response, TransformStream

        transform_stream = TransformStream.new()
        self.writer = transform_stream.writable.getWriter()
        self.result.set_result(
            Response.new(transform_stream.readable, **self._response_init())
        )

    def _respond(self, body: Any) -> None:
        from js import Response
        from pyodide.ffi import create_proxy

        if self.status in NULL_BODY_STATUSES:
            data = None
        else:
            px = create_proxy(body)
            buf = px.getBuffer()
            px.destroy()
            data = buf.data

        self.result.set_result(Response.new(data, **self._response_init()))
        self.finished_response.set()

    def _response_init(self) -> dict[str, Any]:
        return {
            "headers": _to_js_headers(self.headers),
            "status": self.status,
            **body_encoding(self.headers or []),
        }

    async def run(
        self, req: "Request | js.Request", env: Any, state: dict[str, Any] | None
    ) -> None:
        try:
            scope = request_to_scope(req, env, state=state)
            await self.app(scope, self.receive, self.send)
            # If no response has been set yet, the app didn't generate one.
            if not self.result.done():
                raise RuntimeError("The application did not generate a response")
        except Exception as e:
            if not self.result.done():
                self.result.set_exception(e)
                if self.writer is not None:
                    await self.writer.close()
                self.finished_response.set()
                return

            # Response already sent — exception can't be propagated to the
            # client, so log it to avoid silently swallowing errors.
            logger.exception("Exception in ASGI application after response started")
            self.finished_response.set()
            if self.writer is not None and not self.writer_closed:
                # End the body so a client reading it sees the stream stop
                # instead of waiting forever for chunks that will never come.
                # Deliberately not awaited: close() settles only once the
                # queued chunks reach a consumer, so a response body that
                # nobody reads would block this task, which the runtime is
                # waiting on through wait_until.
                run_in_background(close_stream_quietly(self.writer))


async def process_request(
    app: Any,
    req: "Request | js.Request",
    env: Any,
    # Unused, kept for the stock signature.
    ctx: "Context | ExecutionContext | None",
    state: dict[str, Any] | None = None,
) -> tuple[js.Response, Future[None]]:
    cycle = _HttpCycle(app)
    await cycle.queue_body(req)
    request_task = create_task(cycle.run(req, env, state))

    try:
        response = await cycle.result
    except Exception:
        # The request task handles application exceptions and should be allowed
        # to finish before its error is propagated to fetch().
        await request_task
        raise

    # Buffered responses do not depend on a consumer, so request cleanup can
    # finish before returning. Streaming responses must run alongside the
    # consumer because writes can be subject to backpressure.
    if cycle.writer is None:
        await request_task

    return response, request_task


def _wait_until(
    ctx: "Context | ExecutionContext | None", awaitable: Awaitable[Any]
) -> None:
    if ctx is None:
        from workers import wait_until

        wait_until(awaitable)
    else:
        ctx.waitUntil(js_promise(awaitable))


class _WebSocketSession:
    """Bridges a Workers server WebSocket to an ASGI app's receive and send."""

    def __init__(self, server: Any) -> None:
        self.server = server
        self.queue: Queue[dict[str, Any]] = Queue()
        self.app_closed = False

    def onopen(self, evt: Any) -> None:
        self.queue.put_nowait({"type": "websocket.connect"})

    def onclose(self, evt: Any) -> None:
        # Client-initiated closes surface to the app as "websocket.disconnect"
        # per the ASGI spec ("websocket.close" is the app->server direction);
        # frameworks raise their disconnect exceptions only on this type.
        self.queue.put_nowait(
            {"type": "websocket.disconnect", "code": evt.code, "reason": evt.reason}
        )

    def onmessage(self, evt: Any) -> None:
        data = evt.data
        if isinstance(data, str):
            self.queue.put_nowait({"type": "websocket.receive", "text": data})
        else:
            # Binary frames arrive as an ArrayBuffer; the ASGI spec requires
            # them under "bytes" (frameworks like Starlette dispatch on which
            # key is present, so labeling them "text" breaks receive_bytes()).
            self.queue.put_nowait(
                {"type": "websocket.receive", "bytes": data.to_bytes()}
            )

    async def receive(self) -> dict[str, Any]:
        return await self.queue.get()

    async def send(self, got: dict[str, Any]) -> None:
        if got["type"] == "websocket.accept":
            # The Workers WebSocketPair is accepted before the upgrade response
            # is returned, so there is no deferred accept operation here.
            return
        if got["type"] == "websocket.send":
            self._send_frame(got.get("bytes"), got.get("text"))
            return
        if got["type"] == "websocket.close":
            self.app_closed = True
            self.server.close(got.get("code", 1000), got.get("reason", ""))
            return
        logger.warning(" == Not implemented %s", got["type"])

    def _send_frame(self, data: bytes | None, text: str | None) -> None:
        if data is not None:
            with acquire_js_buffer(data) as jsbytes:
                # Unlike the `Response` constructor,  server.send seems to
                # eagerly copy the source buffer
                self.server.send(jsbytes)
        if text is not None:
            self.server.send(text)

    def close_transport(self, finished: Future) -> None:
        if self.app_closed:
            return

        # The app ended without closing, so a half-open connection would leave
        # the client hanging instead of reconnecting.
        failed = not finished.cancelled() and finished.exception() is not None
        # The peer may already have closed the socket.
        with suppress(Exception):
            self.server.close(1011 if failed else 1000, "")


async def process_websocket(
    app: Any,
    req: "Request | js.Request",
    env: Any = None,
    ctx: "Context | ExecutionContext | None" = None,
) -> js.Response:
    from js import Response, WebSocketPair

    client, server = WebSocketPair.new().object_values()
    server.accept()
    # Binary frames otherwise arrive as Blob (observed under wrangler dev),
    # which cannot be read synchronously in the message callback.
    server.binaryType = "arraybuffer"

    session = _WebSocketSession(server)
    # onopen doesn't seem to get called. WS lifecycle events are a bit messed up
    # here.
    session.onopen(1)
    server.onopen = session.onopen
    server.onclose = session.onclose
    server.onmessage = session.onmessage

    scope = request_to_scope(req, env if env is not None else {}, ws=True)
    task = run_in_background(app(scope, session.receive, session.send))
    task.add_done_callback(session.close_transport)
    _wait_until(ctx, task)

    return Response.new(None, status=101, webSocket=client)


async def fetch(
    app: Any,
    req: "Request | js.Request",
    env: Any,
    ctx: "Context | ExecutionContext | None" = None,
) -> js.Response:
    logger.debug("ASGI request: %s %s", req.method, req.url)
    shutdown, state = await start_application(app)

    try:
        result, request_task = await process_request(app, req, env, ctx, state=state)
    except Exception:
        logger.exception("ASGI request failed")
        await shutdown()
        raise

    if request_task.done():
        await shutdown()
    else:

        async def finalize_request():
            try:
                await request_task
            finally:
                await shutdown()

        _wait_until(ctx, run_in_background(finalize_request()))

    return result


async def websocket(
    app: Any,
    req: "Request | js.Request",
    env: Any = None,
    ctx: "Context | ExecutionContext | None" = None,
) -> js.Response:
    return await process_websocket(app, req, env, ctx)


def entrypoint(app: Any) -> type[WorkerEntrypoint]:
    """Create the default Worker entrypoint for an ASGI application."""

    class Default(WorkerEntrypoint):
        async def fetch(self, request):
            # getattr: stock's tests build this class without calling __init__.
            ctx = getattr(self, "ctx", None)
            if (request.headers.get("upgrade") or "").lower() == "websocket":
                return await websocket(app, request, self.env, ctx)
            return await fetch(app, request, self.env, ctx)

    return Default


def __getattr__(name):
    if name == "env":
        from fastapi import Depends, Request  # ty: ignore[unresolved-import]

        @Depends
        async def env(request: Request):
            return request.scope["env"]

        return env

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
