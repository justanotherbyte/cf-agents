from typing import Any, Literal, overload

from ..rpc import python_from_rpc, python_to_rpc
from .base import Binding, _js_options

__all__ = ["KVNamespace"]

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
