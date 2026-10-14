from collections.abc import Iterable
from typing import Any

from ..rpc import python_from_rpc, python_to_rpc
from .base import Binding, _js_options

__all__ = ["R2Bucket", "R2Object", "R2ObjectBody"]


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
