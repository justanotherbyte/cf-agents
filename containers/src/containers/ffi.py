"""
Helpers for crossing the Python/JavaScript boundary inside Pyodide.

This is the only module in the package that imports ``js`` or ``pyodide``.
"""

import asyncio
import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, cast

from js import AbortController, IdentityTransformStream, Object, WebSocketPair
from pyodide.ffi import JsProxy, create_once_callable, create_proxy, to_js

try:
    from pyodide.ffi import jsnull
except ImportError:
    jsnull = None

__all__ = [
    "AbortController",
    "IdentityTransformStream",
    "JsProxy",
    "Object",
    "WebSocketPair",
    "add_event_listener",
    "block_concurrency_while",
    "jsnull",
    "rpc_kwargs",
    "to_js_object",
]

_listener_tasks: set[asyncio.Task[Any]] = set()


def to_js_object(value: dict[str, Any]) -> JsProxy:
    """
    Convert a dict into a plain JavaScript object.

    Parameters
    ----------
    value
        The dict to convert. Nested dicts and lists are converted too.

    Returns
    -------
    JsProxy
        A plain JavaScript object.
    """
    return to_js(value, dict_converter=Object.fromEntries)


def block_concurrency_while[T](
    ctx: Any, fn: Callable[[], Awaitable[T]]
) -> Awaitable[T]:
    """
    Run ``fn`` inside ``ctx.blockConcurrencyWhile``.

    Parameters
    ----------
    ctx
        The Durable Object context.
    fn
        An async callable taking no arguments.

    Returns
    -------
    Awaitable
        Resolves with the result of ``fn``.
    """
    return ctx.blockConcurrencyWhile(create_once_callable(fn))


def add_event_listener(
    target: Any, event: str, callback: Callable[[Any], object]
) -> JsProxy:
    """
    Add a Python callback as an event listener on a JavaScript event target.

    Coroutines returned by ``callback`` are scheduled as tasks, since nothing on
    the JavaScript side awaits them.

    Parameters
    ----------
    target
        The JavaScript event target, e.g. a ``WebSocket`` or ``AbortSignal``.
    event
        The event name.
    callback
        Called with the JavaScript event.

    Returns
    -------
    JsProxy
        The listener proxy, for use with ``removeEventListener``.
    """

    def listener(event_object: Any) -> None:
        result = callback(event_object)
        if inspect.iscoroutine(result):
            task = asyncio.ensure_future(result)
            _listener_tasks.add(task)
            task.add_done_callback(_listener_tasks.discard)

    proxy = create_proxy(listener)
    target.addEventListener(event, proxy)
    return proxy


def rpc_kwargs[**P, R](method: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """
    Accept keyword arguments sent over Durable Object RPC.

    JavaScript RPC has no keyword arguments, so Pyodide sends them as a trailing
    object. This unpacks a trailing dict positional argument back into keyword
    arguments. Only use it on methods that never take a dict positionally.

    Parameters
    ----------
    method
        The async method to wrap.

    Returns
    -------
    Callable
        The wrapped method.
    """

    @functools.wraps(method)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        if args and not kwargs and isinstance(args[-1], dict):
            unpacked = cast(Callable[..., Awaitable[R]], method)
            return await unpacked(*args[:-1], **args[-1])
        return await method(*args, **kwargs)

    return wrapper
