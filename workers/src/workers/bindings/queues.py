from collections.abc import Iterable
from typing import Any

from ..rpc import python_to_rpc
from .base import Binding, _js_options

__all__ = ("Queue",)


class Queue[T](Binding):
    """A queue producer. `T` is the message body type `send` accepts."""

    async def send(
        self,
        body: T,
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
