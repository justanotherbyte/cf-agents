"""The context an entrypoint receives as `ctx`.

As in the stock SDK, names these classes don't define pass through raw: the
JS value comes back unconverted. Python wrappers passed as arguments are
unwrapped to their JS objects first.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import cached_property
from typing import Any

import js
from pyodide.ffi import create_once_callable, create_proxy

from .bindings import Binding, _js_options
from .rpc import python_from_rpc, python_to_rpc


class DurableObjectAbort(BaseException):
    """Raised by `ctx.abort()`. A BaseException, so `except Exception` lets it
    through, and the Durable Object is reset."""


class _RawContext(Binding):
    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name == "js_object":
            raise AttributeError(name)
        value = getattr(self.js_object, name)
        if not callable(value):
            return value
        return lambda *args, **kwargs: value(
            *map(_unwrap, args), **{k: _unwrap(v) for k, v in kwargs.items()}
        )

    def wait_until(self, awaitable: Awaitable[Any]) -> None:
        """Keep the invocation alive until `awaitable`, a coroutine or task,
        finishes."""
        # workerd's patchWaitUntil does this for an ExecutionContext only: JS
        # waits on a real promise that follows the task through a persistent
        # proxy, and the proxy is destroyed once the promise settles.
        proxy = create_proxy(asyncio.ensure_future(awaitable))
        promise = js.Promise.resolve(proxy)
        self.js_object.waitUntil(promise.finally_(create_once_callable(proxy.destroy)))


def _unwrap(value: Any) -> Any:
    return getattr(value, "js_object", value)


class ExecutionContext(_RawContext):
    """The `ctx` of a WorkerEntrypoint."""

    def pass_through_on_exception(self) -> None:
        self.js_object.passThroughOnException()


class DurableObjectStorage(Binding):
    """A Durable Object's storage. Unlisted names, such as `sql`, pass through
    with values converted, as in the stock SDK."""

    async def get(
        self,
        keys: Any,
        options: Any = None,
        /,
        *,
        allow_concurrency: bool | None = None,
        no_cache: bool | None = None,
        **extra: Any,
    ) -> Any:
        """One value for a key, or a dict for a list of keys."""
        js_options = _js_options(
            options, extra, allow_concurrency=allow_concurrency, no_cache=no_cache
        )
        return python_from_rpc(
            await self.js_object.get(python_to_rpc(keys), js_options)
        )

    async def put(
        self,
        key_or_entries: Any,
        *args: Any,
        allow_concurrency: bool | None = None,
        allow_unconfirmed: bool | None = None,
        no_cache: bool | None = None,
        **extra: Any,
    ) -> None:
        """`put(key, value[, options])` or `put(entries[, options])`."""
        named = {
            "allow_concurrency": allow_concurrency,
            "allow_unconfirmed": allow_unconfirmed,
            "no_cache": no_cache,
        }
        if isinstance(key_or_entries, str):
            if not 1 <= len(args) <= 2:
                raise TypeError("put(key, value[, options]) needs a value")
            value, options = args[0], args[1] if len(args) == 2 else None
            js_value = python_to_rpc(value)
            js_options = _js_options(options, extra, **named)
            await self.js_object.put(key_or_entries, js_value, js_options)
        else:
            if len(args) > 1:
                raise TypeError("put(entries[, options]) takes no value")
            js_options = _js_options(args[0] if args else None, extra, **named)
            await self.js_object.put(python_to_rpc(key_or_entries), js_options)

    async def delete(
        self,
        keys: Any,
        options: Any = None,
        /,
        *,
        allow_unconfirmed: bool | None = None,
        no_cache: bool | None = None,
        **extra: Any,
    ) -> Any:
        """Whether a key existed, or how many of a list of keys did."""
        js_options = _js_options(
            options, extra, allow_unconfirmed=allow_unconfirmed, no_cache=no_cache
        )
        return await self.js_object.delete(python_to_rpc(keys), js_options)

    async def delete_all(self, options: Any = None, /, **extra: Any) -> None:
        await self.js_object.deleteAll(_js_options(options, extra))

    async def list(
        self,
        options: Any = None,
        /,
        *,
        start: str | None = None,
        start_after: str | None = None,
        end: str | None = None,
        prefix: str | None = None,
        reverse: bool | None = None,
        limit: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        js_options = _js_options(
            options,
            extra,
            start=start,
            start_after=start_after,
            end=end,
            prefix=prefix,
            reverse=reverse,
            limit=limit,
        )
        return python_from_rpc(await self.js_object.list(js_options))

    async def get_alarm(self, options: Any = None, /, **extra: Any) -> int | None:
        """The scheduled alarm time in milliseconds since the epoch."""
        return python_from_rpc(
            await self.js_object.getAlarm(_js_options(options, extra))
        )

    async def set_alarm(
        self, scheduled_time: datetime | int, options: Any = None, /, **extra: Any
    ) -> None:
        """Schedule `alarm` at a datetime or at milliseconds since the epoch."""
        if isinstance(scheduled_time, datetime):
            scheduled_time = int(scheduled_time.timestamp() * 1000)
        js_options = _js_options(options, extra)
        await self.js_object.setAlarm(scheduled_time, js_options)

    async def delete_alarm(self, options: Any = None, /, **extra: Any) -> None:
        await self.js_object.deleteAlarm(_js_options(options, extra))


class DurableObjectContext(_RawContext):
    """The `ctx` of a Durable Object: the JS DurableObjectState."""

    @cached_property
    def storage(self) -> DurableObjectStorage:
        return DurableObjectStorage(self.js_object.storage)

    @property
    def id(self) -> Any:
        return self.js_object.id

    async def block_concurrency_while(
        self, callback: Callable[[], Awaitable[Any]]
    ) -> Any:
        return python_from_rpc(await self.js_object.blockConcurrencyWhile(callback))

    def accept_web_socket(self, ws: Any, tags: list[str] | None = None) -> None:
        if tags is None:
            self.js_object.acceptWebSocket(_unwrap(ws))
        else:
            self.js_object.acceptWebSocket(_unwrap(ws), python_to_rpc(tags))

    def get_web_sockets(self, tag: str | None = None) -> list[Binding]:
        sockets = self.js_object.getWebSockets(*([] if tag is None else [tag]))
        return [Binding(socket) for socket in sockets]

    def abort(self, reason: str | None = None) -> None:
        """Reset the Durable Object, and stop this call.

        The JS abort ends execution at once, so it runs in a microtask, after
        Python has unwound. DurableObjectAbort stops the caller's code first.
        """
        ctx = self.js_object
        if reason is None:
            js.queueMicrotask(create_once_callable(lambda: ctx.abort()))
        else:
            js.queueMicrotask(create_once_callable(lambda: ctx.abort(reason)))
        raise DurableObjectAbort(reason or "Durable Object abort requested")
