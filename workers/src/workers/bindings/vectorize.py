from collections.abc import Iterable
from typing import Any, Literal

from ..rpc import python_from_rpc, python_to_rpc
from .base import Binding, _js_options

__all__ = ("Vectorize",)

type _MetadataLevel = bool | Literal["all", "indexed", "none"]


class Vectorize(Binding):
    async def describe(self) -> dict[str, Any]:
        return python_from_rpc(await self.js_object.describe())

    async def query(
        self,
        vector: Iterable[float],
        options: Any = None,
        *,
        top_k: int | None = None,
        namespace: str | None = None,
        return_values: bool | None = None,
        return_metadata: _MetadataLevel | None = None,
        filter: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        js_options = _js_options(
            options,
            extra,
            top_k=top_k,
            namespace=namespace,
            return_values=return_values,
            return_metadata=return_metadata,
            filter=filter,
        )
        js_vector = python_to_rpc(list(vector))
        return python_from_rpc(await self.js_object.query(js_vector, js_options))

    async def query_by_id(
        self,
        vector_id: str,
        options: Any = None,
        *,
        top_k: int | None = None,
        namespace: str | None = None,
        return_values: bool | None = None,
        return_metadata: _MetadataLevel | None = None,
        filter: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        js_options = _js_options(
            options,
            extra,
            top_k=top_k,
            namespace=namespace,
            return_values=return_values,
            return_metadata=return_metadata,
            filter=filter,
        )
        return python_from_rpc(await self.js_object.queryById(vector_id, js_options))

    async def insert(self, vectors: Iterable[dict[str, Any]]) -> dict[str, Any]:
        return python_from_rpc(
            await self.js_object.insert(python_to_rpc(list(vectors)))
        )

    async def upsert(self, vectors: Iterable[dict[str, Any]]) -> dict[str, Any]:
        return python_from_rpc(
            await self.js_object.upsert(python_to_rpc(list(vectors)))
        )

    async def delete_by_ids(self, ids: str | Iterable[str]) -> dict[str, Any]:
        return python_from_rpc(await self.js_object.deleteByIds(_js_ids(ids)))

    async def get_by_ids(self, ids: str | Iterable[str]) -> list[dict[str, Any]]:
        return python_from_rpc(await self.js_object.getByIds(_js_ids(ids)))


def _js_ids(ids: str | Iterable[str]) -> Any:
    # One id as a string, as R2Bucket.delete takes one key.
    return python_to_rpc([ids] if isinstance(ids, str) else list(ids))
