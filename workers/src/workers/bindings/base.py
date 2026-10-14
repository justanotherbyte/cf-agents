"""`Binding`, the passthrough wrapper every binding class builds on, and the
conversions its methods share."""

import re
from collections.abc import Iterator
from typing import Any, Self

from .._js import js_name
from ..response import RESPONSE_ACCEPTED_TYPES
from ..rpc import python_from_rpc, python_to_rpc

__all__ = ["Binding"]

# JS values that stay raw when they come back from a binding, as in the stock
# SDK: they are meant for Response() and friends, have Python twins, or are
# promises Python awaits.
_RAW_JS_TYPES = RESPONSE_ACCEPTED_TYPES | {"Headers", "Promise"}


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
        # Python's protocol lookups stay off JS. A JS name such as
        # `__unsafe_ensureInitialized` has no trailing `__` and passes through.
        if (name.startswith("__") and name.endswith("__")) or name == "js_object":
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
