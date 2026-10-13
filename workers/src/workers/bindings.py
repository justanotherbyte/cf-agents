"""Typed classes for Worker bindings, and `Binding` for every other kind.

A name a class doesn't define passes through to the JS object under its exact
JS name, with arguments and results converted as for RPC. Typed methods take
snake_case keywords and forward any keyword they don't know into the JS
options object unchanged, so stock calls like `kv.put(k, v, expirationTtl=60)`
keep working.
"""

import re
from collections.abc import Callable, Iterable, Iterator
from typing import Any, Literal, Self, overload

from ._js import js_name
from .fetch import fetch
from .response import RESPONSE_ACCEPTED_TYPES, Response
from .rpc import python_from_rpc, python_to_rpc

# JS values that stay raw when they come back from a binding, as in the stock
# SDK: they are meant for Response() and friends, have Python twins, or are
# promises Python awaits.
_RAW_JS_TYPES = RESPONSE_ACCEPTED_TYPES | {"Headers", "Promise"}

__all__ = [
    "Binding",
    "D1Database",
    "D1PreparedStatement",
    "DurableObjectNamespace",
    "Fetcher",
    "KVNamespace",
    "Queue",
    "R2Bucket",
    "R2Object",
    "R2ObjectBody",
    "Workflow",
    "WorkflowInstance",
]


class Binding:
    """A binding with no typed class. Every name passes through to JS."""

    def __new__(cls, js_object: Any) -> Self:
        # Wrapping a wrapper of this class returns it unchanged.
        return js_object if isinstance(js_object, cls) else super().__new__(cls)

    def __init__(self, js_object: Any) -> None:
        if js_object is not self:
            self.js_object = js_object

    def __repr__(self) -> str:
        return f"{type(self).__name__}({js_name(self.js_object)})"

    @property
    def _binding(self) -> Any:
        # The stock SDK's private name for the JS object, which its tests read.
        return self.js_object

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name == "js_object":
            raise AttributeError(name)
        return self._pass_through(name, getattr(self.js_object, name))

    def _pass_through(self, name: str, value: Any) -> Any:
        """A JS attribute's value as Python sees it, for a name the class
        doesn't define. Subclasses override this to convert differently."""
        if not callable(value):
            # Read on every access. The stock SDK caches the first value.
            return _convert(value)
        return lambda *args, **kwargs: _invoke(value, args, kwargs)

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return _convert(self.js_object[key])
        return _convert(getattr(self.js_object, key))

    def __iter__(self) -> Iterator[Any]:
        if not hasattr(self.js_object, "__iter__"):
            raise TypeError(f"'{js_name(self.js_object)}' object is not iterable")
        for item in self.js_object:
            yield _convert(item)

    def __bool__(self) -> bool:
        return True

    def __len__(self) -> int:
        length = getattr(self.js_object, "length", None)
        # An RPC stub answers every attribute, so check for a real number.
        if not isinstance(length, int):
            raise TypeError(f"'{js_name(self.js_object)}' object has no len()")
        return length


class _RpcStub(Binding):
    """A function or RpcTarget received over RPC."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return _invoke(self.js_object, args, kwargs)


def _invoke(function: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    result = function(
        *map(python_to_rpc, args), **{k: python_to_rpc(v) for k, v in kwargs.items()}
    )
    if callable(getattr(result, "then", None)):

        async def settle() -> Any:
            return _convert(await result)

        return settle()
    return _convert(result)


def _convert(value: Any) -> Any:
    converted = python_from_rpc(value)
    if isinstance(converted, list):
        return [_wrap(item) for item in converted]
    return _wrap(converted)


def _wrap(value: Any) -> Any:
    """Wrap a JS value that conversion left as JS, so its names pass through."""
    name = js_name(value)
    if name is None or name in _RAW_JS_TYPES:
        return value
    return _RpcStub(value) if callable(value) else Binding(value)


def _js_options(
    given: Any = None, extra: dict[str, Any] | None = None, **named: Any
) -> Any:
    """One JS options object from a typed method's arguments.

    `given` is the options argument in the stock SDK's position: None, a dict
    of JS names, or a JS object. The snake_case `named` options become
    camelCase and unset ones are left out. `extra` holds the caller's other
    keywords, forwarded unchanged.
    """
    named = {_camel(key): value for key, value in named.items() if value is not None}
    if given is not None and not isinstance(given, dict):
        if named or extra:
            raise TypeError("pass options as a JS object or as keywords, not both")
        return given
    return python_to_rpc(named | (given or {}) | (extra or {}))


def _camel(name: str) -> str:
    return re.sub(r"_(\w)", lambda match: match.group(1).upper(), name)


# KV

type _KVType = Literal["text", "json", "arrayBuffer", "stream"]


class KVNamespace(Binding):
    @overload
    async def get(
        self,
        key: str,
        type: Literal["text"] = "text",
        /,
        *,
        cache_ttl: int | None = None,
        **options: Any,
    ) -> str | None: ...
    @overload
    async def get(
        self,
        key: str,
        type: Literal["json"],
        /,
        *,
        cache_ttl: int | None = None,
        **options: Any,
    ) -> Any: ...
    @overload
    async def get(
        self,
        key: str,
        type: Literal["arrayBuffer"],
        /,
        *,
        cache_ttl: int | None = None,
        **options: Any,
    ) -> memoryview | None: ...
    @overload
    async def get(
        self,
        key: Any,
        type: str | dict[str, Any] | Any = "text",
        /,
        *,
        cache_ttl: int | None = None,
        **options: Any,
    ) -> Any: ...
    async def get(
        self,
        key: Any,
        type: Any = "text",
        /,
        *,
        cache_ttl: int | None = None,
        **options: Any,
    ) -> Any:
        js_options = _kv_get_options(type, options, cache_ttl)
        # An arrayBuffer arrives as a memoryview, as in the stock SDK.
        return python_from_rpc(await self.js_object.get(python_to_rpc(key), js_options))

    async def get_with_metadata(
        self,
        key: Any,
        type: _KVType | dict[str, Any] | Any = "text",
        /,
        *,
        cache_ttl: int | None = None,
        **options: Any,
    ) -> dict[str, Any]:
        js_options = _kv_get_options(type, options, cache_ttl)
        result = await self.js_object.getWithMetadata(python_to_rpc(key), js_options)
        return python_from_rpc(result)

    async def put(
        self,
        key: str,
        value: Any,
        options: Any = None,
        /,
        *,
        expiration: int | None = None,
        expiration_ttl: int | None = None,
        metadata: Any = None,
        **extra: Any,
    ) -> None:
        js_options = _js_options(
            options,
            extra,
            expiration=expiration,
            expiration_ttl=expiration_ttl,
            metadata=metadata,
        )
        await self.js_object.put(key, python_to_rpc(value), js_options)

    async def delete(self, key: str) -> None:
        await self.js_object.delete(key)

    async def list(
        self,
        options: Any = None,
        /,
        *,
        prefix: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        js_options = _js_options(
            options, extra, prefix=prefix, limit=limit, cursor=cursor
        )
        return python_from_rpc(await self.js_object.list(js_options))


def _kv_get_options(type: Any, extra: dict[str, Any], cache_ttl: int | None) -> Any:
    # The second argument is a type name or, as in JS, an options object.
    given, kv_type = (None, type) if isinstance(type, str) else (type, None)
    return _js_options(given, extra, type=kv_type, cache_ttl=cache_ttl)


# D1


class D1PreparedStatement(Binding):
    def bind(self, *values: Any) -> "D1PreparedStatement":
        return D1PreparedStatement(self.js_object.bind(*map(python_to_rpc, values)))

    async def first(self, column: str | None = None) -> Any:
        if column is None:
            return python_from_rpc(await self.js_object.first())
        return python_from_rpc(await self.js_object.first(column))

    async def all(self) -> dict[str, Any]:
        return python_from_rpc(await self.js_object.all())

    async def run(self) -> dict[str, Any]:
        return python_from_rpc(await self.js_object.run())

    async def raw(
        self,
        options: Any = None,
        /,
        *,
        column_names: bool | None = None,
        **extra: Any,
    ) -> list[list[Any]]:
        js_options = _js_options(options, extra, column_names=column_names)
        return python_from_rpc(await self.js_object.raw(js_options))


class D1Database(Binding):
    def prepare(self, query: str) -> D1PreparedStatement:
        return D1PreparedStatement(self.js_object.prepare(query))

    async def batch(
        self, statements: Iterable[D1PreparedStatement]
    ) -> list[dict[str, Any]]:
        js_statements = python_to_rpc(list(statements))
        return python_from_rpc(await self.js_object.batch(js_statements))

    async def exec(self, query: str) -> dict[str, Any]:
        return python_from_rpc(await self.js_object.exec(query))


# R2


class R2Object(Binding):
    @property
    def key(self) -> str:
        return self.js_object.key

    @property
    def version(self) -> str:
        return self.js_object.version

    @property
    def size(self) -> int:
        return self.js_object.size

    @property
    def etag(self) -> str:
        return self.js_object.etag

    @property
    def http_etag(self) -> str:
        return self.js_object.httpEtag

    @property
    def uploaded(self) -> Any:
        return python_from_rpc(self.js_object.uploaded)

    @property
    def http_metadata(self) -> dict[str, Any]:
        return python_from_rpc(self.js_object.httpMetadata)

    @property
    def custom_metadata(self) -> dict[str, str]:
        return python_from_rpc(self.js_object.customMetadata)


class R2ObjectBody(R2Object):
    @property
    def body(self) -> Any:
        return self.js_object.body

    @property
    def body_used(self) -> bool:
        return self.js_object.bodyUsed

    async def text(self) -> str:
        return await self.js_object.text()

    async def bytes(self) -> bytes:
        return (await self.js_object.arrayBuffer()).to_bytes()

    async def json(self) -> Any:
        return python_from_rpc(await self.js_object.json())


def _r2_object(value: Any) -> Any:
    value = python_from_rpc(value)
    if value is None:
        return None
    return R2ObjectBody(value) if hasattr(value, "body") else R2Object(value)


class R2Bucket(Binding):
    async def head(self, key: str) -> R2Object | None:
        return _r2_object(await self.js_object.head(key))

    async def get(
        self, key: str, options: Any = None, /, **extra: Any
    ) -> R2ObjectBody | R2Object | None:
        # An R2Object without a body when an onlyIf condition fails.
        js_options = _js_options(options, extra)
        return _r2_object(await self.js_object.get(key, js_options))

    async def put(
        self,
        key: str,
        value: Any,
        options: Any = None,
        /,
        *,
        http_metadata: dict[str, Any] | None = None,
        custom_metadata: dict[str, str] | None = None,
        **extra: Any,
    ) -> R2Object | None:
        js_options = _js_options(
            options,
            extra,
            http_metadata=http_metadata,
            custom_metadata=custom_metadata,
        )
        return _r2_object(
            await self.js_object.put(key, python_to_rpc(value), js_options)
        )

    async def delete(self, keys: str | Iterable[str]) -> None:
        await self.js_object.delete(
            keys if isinstance(keys, str) else python_to_rpc(list(keys))
        )

    async def list(
        self,
        options: Any = None,
        /,
        *,
        prefix: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        delimiter: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        js_options = _js_options(
            options,
            extra,
            prefix=prefix,
            limit=limit,
            cursor=cursor,
            delimiter=delimiter,
        )
        listing = python_from_rpc(await self.js_object.list(js_options))
        listing["objects"] = [R2Object(item) for item in listing.get("objects", [])]
        return listing


# Queues


class Queue(Binding):
    async def send(
        self,
        body: Any,
        options: Any = None,
        /,
        *,
        content_type: str | None = None,
        delay_seconds: int | None = None,
        **extra: Any,
    ) -> None:
        js_options = _js_options(
            options, extra, content_type=content_type, delay_seconds=delay_seconds
        )
        await self.js_object.send(python_to_rpc(body), js_options)

    async def send_batch(
        self,
        messages: Iterable[dict[str, Any]],
        options: Any = None,
        /,
        *,
        delay_seconds: int | None = None,
        **extra: Any,
    ) -> None:
        js_options = _js_options(options, extra, delay_seconds=delay_seconds)
        await self.js_object.sendBatch(python_to_rpc(list(messages)), js_options)


# Services and Durable Objects


class Fetcher(Binding):
    """A service binding, the assets binding, or a Durable Object stub.

    `fetch` returns a crosswind Response. RPC methods pass through.
    """

    async def fetch(self, input: Any, **options: Any) -> Response:
        return await fetch(input, fetcher=self.js_object.fetch, **options)


def _stub(js_stub: Any) -> Any:
    # Callers type it as the Durable Object class, which no Python type can
    # say of a stub (decisions.md#typed-bindings).
    return Fetcher(js_stub)


class DurableObjectNamespace[T](Binding):
    """Names this class doesn't define come back raw, as in the stock SDK, so
    a Durable Object id from `idFromName` stays a JS id."""

    def _pass_through(self, name: str, value: Any) -> Any:
        return value

    def id_from_name(self, name: str) -> Any:
        return self.js_object.idFromName(name)

    def id_from_string(self, id: str) -> Any:
        return self.js_object.idFromString(id)

    def new_unique_id(self, options: Any = None, /, **extra: Any) -> Any:
        return self.js_object.newUniqueId(_js_options(options, extra))

    def get(self, id: Any, options: Any = None, /, **extra: Any) -> T:
        return _stub(self.js_object.get(id, _js_options(options, extra)))

    def get_by_name(self, name: str, options: Any = None, /, **extra: Any) -> T:
        return _stub(self.js_object.getByName(name, _js_options(options, extra)))

    def getByName(self, name: str, *args: Any, **kwargs: Any) -> T:  # noqa: N802
        # The stock SDK's name, which also returns a stub with a Python fetch.
        return _stub(self.js_object.getByName(name, *args, **kwargs))

    def jurisdiction(self, name: str) -> "DurableObjectNamespace[T]":
        return DurableObjectNamespace(self.js_object.jurisdiction(name))


# Workflows


class WorkflowInstance(Binding):
    @property
    def id(self) -> str:
        return self.js_object.id

    async def status(self) -> dict[str, Any]:
        return python_from_rpc(await self.js_object.status())

    async def pause(self) -> None:
        await self.js_object.pause()

    async def resume(self) -> None:
        await self.js_object.resume()

    async def restart(self) -> None:
        await self.js_object.restart()

    async def terminate(self) -> None:
        await self.js_object.terminate()

    async def send_event(
        self,
        event: Any = None,
        /,
        *,
        type: str | None = None,
        payload: Any = None,
        **extra: Any,
    ) -> None:
        js_event = _js_options(event, extra, type=type, payload=payload)
        await self.js_object.sendEvent(js_event)


class Workflow(Binding):
    async def get(self, id: str) -> WorkflowInstance:
        return WorkflowInstance(await self.js_object.get(id))

    async def create(
        self,
        options: Any = None,
        /,
        *,
        id: str | None = None,
        params: Any = None,
        **extra: Any,
    ) -> WorkflowInstance:
        js_options = _js_options(options, extra, id=id, params=params)
        return WorkflowInstance(await self.js_object.create(js_options))

    async def create_batch(self, batch: Iterable[Any]) -> list[WorkflowInstance]:
        instances = await self.js_object.createBatch(python_to_rpc(list(batch)))
        return [WorkflowInstance(instance) for instance in instances]


# The env


_CLASS_FOR_JS: dict[str, Callable[[Any], Any]] = {
    "Fetcher": Fetcher,
    "DurableObjectNamespace": DurableObjectNamespace,
    "WorkflowImpl": Workflow,
    "KvNamespace": KVNamespace,
    "R2Bucket": R2Bucket,
    "D1Database": D1Database,
    "WorkerQueue": Queue,
}


class _EnvWrapper(Binding):
    """The env an entrypoint sees: each binding wrapped by its JS class."""

    @property
    def _env(self) -> Any:
        # The stock SDK's private name for the JS env, which its tests read.
        return self.js_object

    def _pass_through(self, name: str, value: Any) -> Any:
        js_class = js_name(value)
        if js_class is None:
            binding = value  # a text var or secret
        elif js_class in ("Object", "Array"):
            # A JSON var, or a plain-object binding such as version metadata.
            binding = python_from_rpc(value)
        else:
            binding = _CLASS_FOR_JS.get(js_class, Binding)(value)
        # Bindings don't change, so later reads skip __getattr__.
        setattr(self, name, binding)
        return binding
