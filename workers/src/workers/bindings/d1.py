from collections.abc import Iterable
from typing import Any

from ..rpc import python_from_rpc, python_to_rpc
from .base import Binding, _js_options

__all__ = ["D1Database", "D1PreparedStatement"]


class D1PreparedStatement(Binding):
    def bind(self, *values: Any) -> "D1PreparedStatement":
        return D1PreparedStatement(self.js_object.bind(*map(python_to_rpc, values)))

    async def first(self, column: str | None = None) -> Any:
        args = () if column is None else (column,)
        return python_from_rpc(await self.js_object.first(*args))

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
