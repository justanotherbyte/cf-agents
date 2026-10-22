from typing import Any

from ..rpc import python_from_rpc
from .base import Binding, _js_options

__all__ = ("RateLimit",)


class RateLimit(Binding):
    async def limit(
        self, options: Any = None, *, key: str | None = None, **extra: Any
    ) -> dict[str, Any]:
        js_options = _js_options(options, extra, key=key)
        return python_from_rpc(await self.js_object.limit(js_options))
