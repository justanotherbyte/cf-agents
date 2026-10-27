"""Serve a WSGI application, such as Flask, from a Worker.

Ported from the stock SDK at cb2db3c. `wsgi.input` streams the request body
through `run_sync`, which Python 3.12 Workers lack, so there the body is read
up front instead.
"""

import io
import logging
import sys
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from urllib.parse import SplitResult, unquote, urlsplit

import js
import pyodide.ffi

from ._adapter import NULL_BODY_STATUSES, body_encoding, request_headers
from .context import ExecutionContext
from .entrypoints import WorkerEntrypoint
from .request import Request
from .types import Context

logger = logging.getLogger("wsgi")


def _wsgi_native_string(value: str) -> str:
    """Convert a (possibly non-ASCII) Python ``str`` into a WSGI "native string".

    PEP 3333 requires that strings handed to a WSGI application use the
    bytes-in-unicode convention: the string contains characters whose code
    points correspond to the raw bytes (i.e. it is decoded with ``latin-1``).
    Frameworks such as Werkzeug/Flask undo this by re-encoding with ``latin-1``
    and decoding with ``utf-8``.
    """
    return value.encode("utf-8").decode("latin-1")


class _ReadableStreamInput(io.RawIOBase):
    """A blocking, file-like ``wsgi.input`` backed by an async ``ReadableStream``.

    WSGI apps read the request body synchronously, but the Workers body is an
    async ``ReadableStream``. We bridge the two lazily using Pyodide's
    ``run_sync`` (JSPI stack switching): each ``readinto`` pulls only as much
    from the stream as the application asks for, so the body is never fully
    buffered up-front.

    This only works while a JSPI suspender is on the stack, which is the case
    here because the WSGI app runs synchronously inside the async ``fetch``
    handler.
    """

    def __init__(self, js_body: "js.ReadableStream") -> None:
        self._reader = js_body.getReader()
        self._buf = bytearray()
        self._eof = False

    def readable(self) -> bool:
        return True

    def _fill(self) -> None:
        from pyodide.ffi import run_sync

        while not self._buf and not self._eof:
            result = run_sync(self._reader.read())
            if result.done:
                self._eof = True
            elif result.value is not None:
                self._buf.extend(result.value.to_bytes())

    def readinto(self, b) -> int:
        self._fill()
        if not self._buf:
            return 0

        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        del self._buf[:n]
        return n

    def close(self) -> None:
        if not self.closed:
            self._release_reader()
        super().close()

    def __del__(self):
        self.close()

    def _release_reader(self) -> None:
        """Discard the request body and release the underlying reader.

        If the body was fully consumed we just release the lock. Otherwise we
        ``cancel()`` the reader to drop the remainder; ``cancel()`` returns a JS
        promise that the runtime settles on its own, so we don't await it.
        """
        from pyodide.ffi import run_sync

        try:
            if self._eof:
                self._reader.releaseLock()
            else:
                run_sync(self._reader.cancel())
        except Exception:
            logger.debug("Releasing the request body failed", exc_info=True)


def _make_wsgi_input(req: "Request | js.Request") -> "io.BufferedIOBase | None":
    """Build a lazy, stream-backed ``wsgi.input`` if the runtime supports it.

    Returns ``None`` when lazy streaming is unavailable, signalling the caller
    to fall back to pre-buffering the body.
    """
    if not req.body:
        return io.BytesIO(b"")
    if not hasattr(pyodide.ffi, "run_sync"):
        return None

    return io.BufferedReader(_ReadableStreamInput(req.body))


async def _read_body(req: "Request | js.Request") -> bytes:
    """Read the entire request body into memory (fallback when streaming is off)."""
    if not req.body:
        return b""

    chunks = [data.to_bytes() async for data in req.body]
    return b"".join(chunks)


def build_environ(
    req: "Request | js.Request",
    env: dict[str, Any],
    body: "bytes | io.IOBase",
) -> dict[str, Any]:
    url = urlsplit(req.url)
    if not url.scheme:
        raise ValueError(f"Request URL is not absolute: {req.url!r}")

    if isinstance(body, (bytes, bytearray)):
        wsgi_input: Any = io.BytesIO(body)
        buffered_length: int | None = len(body)
    else:
        wsgi_input = body
        buffered_length = None

    environ: dict[str, Any] = {
        "REQUEST_METHOD": str(req.method),
        "SCRIPT_NAME": "",
        # PATH_INFO is the URL-decoded path expressed as a WSGI native string.
        "PATH_INFO": _wsgi_native_string(unquote(url.path or "/")),
        # QUERY_STRING stays percent-encoded per the spec.
        "QUERY_STRING": url.query,
        "SERVER_NAME": url.hostname,
        "SERVER_PORT": _server_port(url),
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": url.scheme,
        "wsgi.input": wsgi_input,
        "wsgi.errors": sys.stderr,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "workers.env": env,
    }
    environ.update(_header_variables(request_headers(req)))

    if "CONTENT_LENGTH" not in environ and buffered_length:
        environ["CONTENT_LENGTH"] = str(buffered_length)

    return environ


def _server_port(url: SplitResult) -> str:
    if url.port:
        return str(url.port)
    return "443" if url.scheme == "https" else "80"


def _header_variables(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    """The environ variables for request headers, per the CGI convention."""
    variables: dict[str, str] = {}
    for key, value in headers:
        name = key.upper().replace("-", "_")
        if name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            variables[name] = value
        elif (http_name := "HTTP_" + name) in variables:
            # Repeated headers are folded into a single comma-separated value.
            variables[http_name] += "," + value
        else:
            variables[http_name] = value

    return variables


def _to_js_uint8array(chunk: bytes) -> "js.Uint8Array":
    """Copy Python ``bytes`` into a standalone, JS-owned ``Uint8Array``.

    A copy (rather than a view over the WASM heap) is required because
    ``ReadableStreamDefaultController.enqueue`` keeps the chunk by reference in
    the stream's internal queue until the consumer reads it later, by which
    point ``chunk`` has been released. ``to_js`` copies buffer data out of the
    heap into a fresh ``ArrayBuffer`` (pyodide's ``python2js_buffer``), so its
    result stays valid.
    """
    from pyodide.ffi import to_js

    return to_js(chunk)


def _close_iterable(iterable: Any) -> None:
    # PEP 3333: if the iterable has a close() method, the server must call it.
    close = getattr(iterable, "close", None)
    if close is not None:
        close()


_END = object()


def _make_streaming_response(
    status: str,
    headers: "list[tuple[str, str]]",
    chunks: "Iterator[bytes]",
    on_close: "Callable[[], None]",
) -> js.Response:
    """Build a ``js.Response`` whose body is pulled lazily from *chunks*.

    Uses a ``ReadableStream`` with a synchronous, pull-based source so response
    bytes are produced on demand as the client reads, instead of buffering the
    whole body in memory. ``on_close`` runs exactly once when the stream is
    exhausted, errored, or cancelled.
    """
    from js import ReadableStream, Response

    init = _response_init(status, headers)
    if init["status"] in NULL_BODY_STATUSES:
        on_close()
        return Response.new(None, **init)

    source = _PullSource(chunks, on_close)
    stream = ReadableStream.new(pull=source.pull, cancel=source.cancel)

    return Response.new(stream, **init)


def _response_init(status: str, headers: "list[tuple[str, str]]") -> dict[str, Any]:
    from js import Headers as JsHeaders

    code, _, reason = status.partition(" ")
    init: dict[str, Any] = {"status": int(code), **body_encoding(headers)}
    if reason:
        init["statusText"] = reason

    init["headers"] = JsHeaders.new()
    for key, value in headers:
        # `append` (not `set`) preserves repeated headers such as Set-Cookie.
        init["headers"].append(key, value)

    return init


class _PullSource:
    """The pull-based source of a response ``ReadableStream``."""

    def __init__(self, chunks: "Iterator[bytes]", on_close: "Callable[[], None]"):
        from pyodide.ffi import create_proxy

        self._chunks = chunks
        self._on_close = on_close
        self._done = False
        # Make proxies async so that it is possible to stack switch inside them.
        self.pull = create_proxy(self._pull)
        self.cancel = create_proxy(self._cancel)

    async def _pull(self, controller: Any) -> None:
        try:
            chunk: Any = next(self._chunks, _END)
        except Exception as exc:
            logger.exception("Exception while streaming WSGI response body")
            self._cleanup()
            controller.error(str(exc))
            return

        if chunk is _END:
            controller.close()
            self._cleanup()
            return

        controller.enqueue(_to_js_uint8array(chunk))

    async def _cancel(self, _reason: Any = None) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        if self._done:
            return

        self._done = True
        try:
            self._on_close()
        finally:
            self.pull.destroy()
            self.cancel.destroy()


class _StartResponse:
    """The ``start_response`` callable, and the status and headers it records."""

    def __init__(self) -> None:
        self.status: str | None = None
        self.headers: list[tuple[str, str]] = []
        self.written: list[bytes] = []

    def __call__(self, status, response_headers, exc_info=None):
        if exc_info is not None:
            try:
                if self.status is not None:
                    # Headers were already sent; re-raise the original error.
                    raise exc_info[1].with_traceback(exc_info[2])
            finally:
                exc_info = None
        elif self.status is not None:
            raise AssertionError("start_response() called more than once")

        self.status = status
        self.headers = response_headers
        return self.write

    def write(self, chunk: bytes) -> None:
        if self.status is None:
            raise AssertionError("write() called before start_response()")
        if chunk:
            self.written.append(chunk)


def process_request(
    app: Any,
    req: "Request | js.Request",
    env: Any,
    body: "bytes | io.IOBase",
) -> js.Response:
    environ = build_environ(req, env, body)
    start_response = _StartResponse()
    result = app(environ, start_response)
    result_iter = filter(None, iter(result))

    def close_all() -> None:
        _close_iterable(result)
        try:
            environ["wsgi.input"].close()
        except Exception:
            logger.exception("Failed to close wsgi.input")

    # WSGI apps must call start_response before yielding the first body chunk,
    # but some defer it until the first non-empty chunk is produced. Pull that
    # chunk now so status/headers are known before we construct the Response.
    try:
        first_chunk: Any = next(result_iter, _END)
    except Exception:
        close_all()
        raise

    if start_response.status is None:
        close_all()
        raise RuntimeError("The WSGI application did not call start_response()")

    def body_chunks() -> "Iterator[bytes]":
        yield from start_response.written
        if first_chunk is not _END:
            yield first_chunk
        yield from result_iter

    return _make_streaming_response(
        start_response.status, start_response.headers, body_chunks(), close_all
    )


async def fetch(
    app: Any,
    req: "Request | js.Request",
    env: Any,
    ctx: "Context | ExecutionContext | None" = None,
) -> js.Response:
    logger.debug("WSGI request: %s %s", req.method, req.url)
    body: bytes | io.IOBase | None = _make_wsgi_input(req)
    if body is None:
        body = await _read_body(req)

    try:
        return process_request(app, req, env, body)
    except Exception:
        logger.exception("WSGI request failed")
        raise


def entrypoint(app: Any) -> type[WorkerEntrypoint]:
    """Create the default Worker entrypoint for a WSGI application."""

    class Default(WorkerEntrypoint):
        async def fetch(self, request):
            return await fetch(app, request, self.env)

    return Default
