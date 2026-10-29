"""
Helpers for crossing the Python/JavaScript boundary inside Pyodide.

This is the only module in the package that imports ``js`` or ``pyodide``.
"""

from collections.abc import Awaitable, Callable
from typing import Any

import js
from pyodide.ffi import create_once_callable, create_proxy, to_js

try:
    from pyodide.ffi import jsnull
except ImportError:
    jsnull = None

__all__ = (
    "abort_controller",
    "fixed_length_stream",
    "header_items",
    "is_absent",
    "is_bytes_chunk",
    "is_readable_stream",
    "js_fetch",
    "json_response",
    "readable_stream",
    "response",
    "to_bytes",
    "to_js_error",
    "to_js_object",
    "to_py",
    "to_uint8array",
    "unwrap",
)


def to_js_object(value: Any) -> Any:
    """Convert dicts and lists into plain JavaScript objects and arrays."""
    return to_js(value, dict_converter=js.Object.fromEntries)


def to_py(value: Any) -> Any:
    """Convert a JavaScript value into Python dicts, lists and scalars."""
    return value.to_py() if hasattr(value, "to_py") else value


def unwrap(value: Any) -> Any:
    """The JavaScript object behind a Python ``workers`` wrapper, if any."""
    # Runtime quirk: binding wrappers keep the JS object in ``_binding`` and
    # forward unknown names to JS, so ``js_object`` would return a JS function.
    binding = getattr(value, "_binding", None)
    return binding if binding is not None else getattr(value, "js_object", value)


def is_absent(value: Any) -> bool:
    return value is None or value is jsnull


def to_bytes(chunk: Any) -> bytes:
    """Convert a ``Uint8Array`` read from a stream into ``bytes``."""
    return bytes(chunk) if isinstance(chunk, bytes | bytearray) else chunk.to_bytes()


def to_uint8array(data: bytes) -> Any:
    return to_js(data)


def is_bytes_chunk(chunk: Any) -> bool:
    if isinstance(chunk, bytes | bytearray):
        return True
    return getattr(getattr(chunk, "constructor", None), "name", None) == "Uint8Array"


def is_readable_stream(value: Any) -> bool:
    return callable(getattr(value, "getReader", None))


def header_items(headers: Any) -> list[tuple[str, str]]:
    """The name and value pairs of a JavaScript ``Headers``, names lowercase."""
    return [(str(name).lower(), str(value)) for name, value in headers]


def abort_controller() -> Any:
    return js.AbortController.new()


def to_js_error(error: BaseException) -> Any:
    """A JavaScript ``Error`` with the exception's class name and message."""
    js_error = js.Error.new(str(error))
    js_error.name = type(error).__name__
    return js_error


def readable_stream(
    pull: Callable[[Any], Awaitable[bool]], cancel: Callable[[Any], None]
) -> Any:
    """
    A ``ReadableStream`` that calls ``pull`` for each chunk it needs.

    ``pull`` returns ``True`` once it has closed or errored the stream. The
    proxies are destroyed then, or after ``cancel``.
    """
    proxies: list[Any] = []

    def destroy() -> None:
        for proxy in proxies:
            proxy.destroy()

    async def pull_once(controller: Any) -> None:
        if await pull(controller):
            js.queueMicrotask(create_once_callable(destroy))

    def cancel_once(reason: Any) -> None:
        try:
            cancel(reason)
        finally:
            js.queueMicrotask(create_once_callable(destroy))

    proxies.extend((create_proxy(pull_once), create_proxy(cancel_once)))
    source = to_js_object({"pull": proxies[0], "cancel": proxies[1]})
    return js.ReadableStream.new(source)


def response(body: Any = None, *, status: int = 200, headers: Any = None) -> Any:
    init: dict[str, Any] = {"status": status}
    if headers is not None:
        init["headers"] = headers
    return js.Response.new(body, to_js_object(init))


def json_response(value: Any) -> Any:
    return js.Response.json(to_js_object(value))


def js_fetch(url: str, init: dict[str, Any]) -> Awaitable[Any]:
    return js.fetch(url, to_js_object(init))


def fixed_length_stream(length: int) -> Any:
    return js.FixedLengthStream.new(length)
