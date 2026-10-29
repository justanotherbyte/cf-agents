"""
Stand-ins for the runtime-only ``js``, ``pyodide.ffi`` and ``workers`` modules,
and a scripted container. ``install()`` must run before ``sandboxes`` is
imported, so ``conftest.py`` calls it first.
"""

import asyncio
import struct
import sys
import types
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

JSNULL = object()


def frame(kind: int, payload: bytes = b"") -> bytes:
    return struct.pack("<4sBBI", b"SBXF", 1, kind, len(payload)) + payload


def success() -> bytes:
    return frame(0)


def data(payload: bytes) -> bytes:
    return frame(2, payload)


def errno_frame(errno: int, detail: str = "") -> bytes:
    return frame(1, struct.pack("<i", errno) + detail.encode())


class Reader:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = list(chunks)
        self.cancelled = False
        self.released = False

    async def read(self) -> SimpleNamespace:
        await asyncio.sleep(0)
        if not self.chunks:
            return SimpleNamespace(done=True, value=None)
        chunk = self.chunks.pop(0)
        if isinstance(chunk, BaseException):
            raise chunk
        if isinstance(chunk, asyncio.Future):
            chunk = await chunk
        return SimpleNamespace(done=False, value=chunk)

    async def cancel(self, *args: Any) -> None:
        self.cancelled = True

    def releaseLock(self) -> None:
        self.released = True


class Stream:
    def __init__(self, chunks: list[Any]) -> None:
        self.reader = Reader(chunks)
        self.cancelled = False

    def getReader(self) -> Reader:
        return self.reader

    async def cancel(self, *args: Any) -> None:
        self.cancelled = True


class Writer:
    def __init__(self, log: list[Any]) -> None:
        self.log = log
        self.written = bytearray()
        self.closed = False
        self.aborted = False

    @property
    def ready(self) -> asyncio.Future[None]:
        future = asyncio.get_running_loop().create_future()
        future.set_result(None)
        return future

    async def write(self, chunk: bytes) -> None:
        self.written += chunk
        self.log.append(("write", bytes(chunk)))

    async def close(self) -> None:
        self.closed = True
        self.log.append(("close",))

    async def abort(self, *args: Any) -> None:
        self.aborted = True

    def getWriter(self) -> "Writer":
        return self

    def releaseLock(self) -> None:
        pass


class Process:
    def __init__(
        self,
        log: list[Any],
        stdout: list[Any] | None = None,
        stderr: list[Any] | None = None,
        exit_code: int = 0,
    ) -> None:
        self.stdout = Stream(stdout or [])
        self.stderr = Stream(stderr or [])
        self.stdin = Writer(log)
        self.exit_code = exit_code
        self.killed: int | None = None

    @property
    def exitCode(self) -> asyncio.Future[int]:
        future = asyncio.get_running_loop().create_future()
        future.set_result(self.exit_code)
        return future

    def kill(self, signal: int) -> None:
        self.killed = signal


class Container:
    """Answers each ``exec`` with the next scripted process."""

    def __init__(self, *processes: Callable[[list[Any]], Process] | Process) -> None:
        self.processes = list(processes)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.log: list[Any] = []
        self.intercepts: list[tuple[str, Any]] = []

    def script(self, **kwargs: Any) -> Process:
        process = Process(self.log, **kwargs)
        self.processes.append(process)
        return process

    async def exec(self, cmd: list[str], options: dict[str, Any]) -> Process:
        self.calls.append((list(cmd), dict(options)))
        self.log.append(("exec", cmd[1:3]))
        return self.processes.pop(0)  # ty: ignore[invalid-return-type]

    async def interceptOutboundHttp(self, host: str, fetcher: Any) -> None:
        self.intercepts.append((host, fetcher))
        self.log.append(("intercept", host, fetcher["props"]["mode"]))


class AbortSignal:
    def __init__(self) -> None:
        self.aborted = False


class AbortController:
    def __init__(self) -> None:
        self.signal = AbortSignal()

    @classmethod
    def new(cls) -> "AbortController":
        return cls()

    def abort(self) -> None:
        self.signal.aborted = True


class JsError(Exception):
    @classmethod
    def new(cls, message: str) -> "JsError":
        return cls(message)


class ReadableStream:
    def __init__(self, source: dict[str, Any]) -> None:
        self.source = source

    @classmethod
    def new(cls, source: dict[str, Any]) -> "ReadableStream":
        return cls(source)

    async def read_all(self) -> bytes:
        controller = SimpleNamespace(chunks=[], closed=False, failure=None)
        controller.enqueue = controller.chunks.append
        controller.close = lambda: setattr(controller, "closed", True)
        controller.error = lambda error: setattr(controller, "failure", error)
        while not controller.closed and controller.failure is None:
            await self.source["pull"](controller)
        if controller.failure is not None:
            raise controller.failure
        return b"".join(controller.chunks)


class Response:
    def __init__(self, body: Any = None, init: dict[str, Any] | None = None) -> None:
        init = init or {}
        self.body = body
        self.status = init.get("status", 200)
        self.headers = init.get("headers", {})
        self.ok = 200 <= self.status < 300

    @classmethod
    def new(cls, body: Any = None, init: dict[str, Any] | None = None) -> "Response":
        return cls(body, init)

    @classmethod
    def json(cls, value: Any) -> "Response":
        return cls(value)


class Proxy:
    def __init__(self, fn: Any) -> None:
        self.fn = fn
        self.destroyed = False

    def __call__(self, *args: Any) -> Any:
        return self.fn(*args)

    def destroy(self) -> None:
        self.destroyed = True


class WorkerEntrypoint:
    def __init__(self, ctx: Any, env: Any) -> None:
        self.ctx = ctx
        self.env = env


def _identity(value: Any, *args: Any, **kwargs: Any) -> Any:
    return value


def _module(name: str, attributes: dict[str, Any]) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


def install() -> None:
    if getattr(sys.modules.get("js"), "_sandboxes_stub", False):
        return

    js = _module(
        "js",
        {
            "_sandboxes_stub": True,
            "Object": SimpleNamespace(fromEntries=dict),
            "AbortController": AbortController,
            "Error": JsError,
            "ReadableStream": ReadableStream,
            "Response": Response,
            "queueMicrotask": lambda fn: fn(),
        },
    )
    ffi = _module(
        "pyodide.ffi",
        {
            "jsnull": JSNULL,
            "to_js": _identity,
            "create_proxy": Proxy,
            "create_once_callable": _identity,
        },
    )
    sys.modules["js"] = js
    sys.modules["pyodide"] = _module("pyodide", {"ffi": ffi})
    sys.modules["pyodide.ffi"] = ffi
    sys.modules["workers"] = _module("workers", {"WorkerEntrypoint": WorkerEntrypoint})
