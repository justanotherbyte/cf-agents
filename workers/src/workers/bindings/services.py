"""Service bindings, the assets binding and Durable Object namespaces."""

from typing import Any

from ..fetch import fetch
from ..response import Response
from .base import Binding, _js_options

__all__ = ("DurableObjectNamespace", "Fetcher")


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
