"""
Stand-ins for the runtime-only ``workers``, ``js`` and ``pyodide.ffi`` modules.

Mirrors ``setup.ts``, which mocks ``cloudflare:workers``. ``install()`` must run
before ``containers`` is imported, so ``conftest.py`` calls it first.
"""

import sys
import types
from typing import Any

JSNULL = object()


class Headers:
    """Case-insensitive headers, like JS ``Headers``."""

    def __init__(self, headers: Any = None) -> None:
        self._headers: dict[str, str] = {}
        items = headers.items() if hasattr(headers, "items") else headers or []
        for key, value in items:
            self._headers[key.lower()] = value

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key.lower() in self._headers

    def __getitem__(self, key: str) -> str:
        return self._headers[key.lower()]

    def get(self, key: str, default: Any = None) -> Any:
        return self._headers.get(key.lower(), default)

    def items(self) -> list[tuple[str, str]]:
        return list(self._headers.items())


class AbortSignal:
    def __init__(self) -> None:
        self.aborted = False
        self._listeners: list[Any] = []

    def addEventListener(self, event: str, listener: Any) -> None:  # noqa: N802
        self._listeners.append(listener)

    def removeEventListener(self, event: str, listener: Any) -> None:  # noqa: N802
        if listener in self._listeners:
            self._listeners.remove(listener)

    def abort(self) -> None:
        self.aborted = True
        for listener in list(self._listeners):
            listener(None)


class AbortController:
    def __init__(self) -> None:
        self.signal = AbortSignal()

    @classmethod
    def new(cls) -> "AbortController":
        return cls()

    def abort(self) -> None:
        self.signal.abort()


class JsResponse:
    """What ``tcp_port.fetch`` resolves with: a stand-in for a JS ``Response``."""

    def __init__(
        self,
        status: int = 200,
        body: Any = None,
        headers: Any = None,
        web_socket: Any = None,
    ) -> None:
        self.status = status
        self.statusText = ""
        self.body = body
        self.headers = Headers(headers)
        self.webSocket = web_socket


class Request:
    def __init__(self, input: "Request | str", **init: Any) -> None:
        if isinstance(input, Request):
            self.url = input.url
            self.method = init.get("method", input.method)
            self.headers = Headers(init.get("headers", input.headers))
            self._body = init.get("body", input._body)
        else:
            self.url = input
            self.method = init.get("method", "GET")
            self.headers = Headers(init.get("headers"))
            self._body = init.get("body")
        self.signal = AbortSignal()

    @property
    def js_object(self) -> "Request":
        return self

    async def text(self) -> str:
        return self._body or ""


class Response:
    def __init__(
        self,
        body: Any = None,
        status: int | None = None,
        status_text: str = "",
        headers: Any = None,
        web_socket: Any = None,
    ) -> None:
        if isinstance(body, JsResponse):
            self.js_object = body
        else:
            self.js_object = JsResponse(
                status=status or 200, body=body, headers=headers, web_socket=web_socket
            )
        self.status_text = status_text

    @property
    def status(self) -> int:
        return self.js_object.status

    @property
    def body(self) -> Any:
        return self.js_object.body

    @property
    def headers(self) -> Headers:
        return self.js_object.headers

    @property
    def web_socket(self) -> Any:
        return self.js_object.webSocket

    async def text(self) -> str:
        return self.body or ""


class DurableObject:
    def __init__(self, ctx: Any, env: Any) -> None:
        self.ctx = ctx
        self.env = env


class WorkerEntrypoint:
    def __init__(self, ctx: Any, env: Any) -> None:
        self.ctx = ctx
        self.env = env


async def fetch(resource: Any, **kwargs: Any) -> Response:
    raise NotImplementedError("fetch is not available in tests")


def _identity(value: Any, *args: Any, **kwargs: Any) -> Any:
    return value


class Proxy:
    """What ``create_proxy`` returns: callable, and destroyable."""

    def __init__(self, fn: Any) -> None:
        self.fn = fn
        self.destroyed = False

    def __call__(self, *args: Any) -> Any:
        return self.fn(*args)

    def destroy(self) -> None:
        self.destroyed = True


class _JsObject:
    @staticmethod
    def fromEntries(entries: Any) -> Any:  # noqa: N802
        return dict(entries)

    @staticmethod
    def values(obj: Any) -> list[Any]:
        return list(obj.values())


class _Constructor:
    """A JS class; tests replace ``new`` where they need to."""

    @staticmethod
    def new(*args: Any) -> Any:
        raise NotImplementedError


def _module(name: str, attributes: dict[str, Any]) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


def install() -> None:
    if getattr(sys.modules.get("js"), "_containers_stub", False):
        return

    js = _module(
        "js",
        {
            "_containers_stub": True,
            "Object": _JsObject,
            "AbortController": AbortController,
            "WebSocketPair": type("WebSocketPair", (_Constructor,), {}),
            "IdentityTransformStream": type(
                "IdentityTransformStream", (_Constructor,), {}
            ),
        },
    )
    ffi = _module(
        "pyodide.ffi",
        {
            "JsProxy": object,
            "jsnull": JSNULL,
            "to_js": _identity,
            "create_proxy": Proxy,
            "create_once_callable": _identity,
        },
    )
    pyodide = _module("pyodide", {"ffi": ffi})
    workers = _module(
        "workers",
        {
            "DurableObject": DurableObject,
            "WorkerEntrypoint": WorkerEntrypoint,
            "Request": Request,
            "Response": Response,
            "fetch": fetch,
            "python_to_rpc": _identity,
            "python_from_rpc": _identity,
        },
    )

    sys.modules["js"] = js
    sys.modules["pyodide"] = pyodide
    sys.modules["pyodide.ffi"] = ffi
    sys.modules["workers"] = workers
