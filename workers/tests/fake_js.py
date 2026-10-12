"""A fake JS layer, so the SDK imports and runs on the host.

It follows Pyodide's conversion rules for containers: to_py turns Array, Object,
Map and Set into list, dict and set and hands every other JS value to the
default converter; to_js does the reverse and hands every Python object it
can't convert to the default converter.
"""

import sys
from collections.abc import Callable
from types import ModuleType, SimpleNamespace
from typing import Any


class JsValue:
    """A JS value: a constructor name, contents for to_py, and any attributes."""

    def __init__(self, name: str, contents: Any = None, **attributes: Any) -> None:
        self.constructor = SimpleNamespace(name=name)
        self.contents = contents
        self.__dict__.update(attributes)

    def __iter__(self) -> Any:
        # Pyodide makes JS arrays and sets iterable from Python.
        if self.constructor.name not in ("Array", "Set"):
            raise TypeError(f"{self.constructor.name} is not iterable")
        return iter(self.contents)

    def to_py(self, *, default_converter: Callable[..., Any] | None = None) -> Any:
        def convert(value: Any) -> Any:
            if not isinstance(value, JsValue):
                return value
            name = value.constructor.name
            if name == "Array":
                return [convert(item) for item in value.contents]
            if name in ("Object", "Map"):
                return {key: convert(item) for key, item in value.contents.items()}
            if name == "Set":
                return {convert(item) for item in value.contents}
            if default_converter is None:
                return value
            return default_converter(value, convert, {})

        return convert(self)


class JsNull:
    """Pyodide's jsnull, which has no constructor to read."""


jsnull = JsNull()


def js_request(url: str, method: str = "GET") -> JsValue:
    return JsValue("Request", url=url, method=method)


def js_response(body: Any = None, init: Any = None, **options: Any) -> JsValue:
    # Pyodide passes Python keyword arguments as a trailing options object.
    return JsValue("Response", body=body, init=init or options, url="")


def js_date(milliseconds: float) -> JsValue:
    return JsValue("Date", getTime=lambda: milliseconds)


def js_error(message: str) -> JsValue:
    return JsValue("Error", message=message, toString=lambda: f"Error: {message}")


def create_proxy(function: Any) -> SimpleNamespace:
    return SimpleNamespace(proxied=function)


def to_js(
    value: Any,
    *,
    dict_converter: Callable[..., Any] | None = None,
    default_converter: Callable[..., Any] | None = None,
) -> Any:
    def convert(item: Any) -> Any:
        if item is None or isinstance(
            item, (str, int, float, bytes, memoryview, JsValue, JsNull)
        ):
            return item
        if isinstance(item, dict):
            entries = [(key, convert(v)) for key, v in item.items()]
            return dict_converter(entries) if dict_converter else dict(entries)
        if isinstance(item, (list, tuple)):
            return [convert(v) for v in item]
        if isinstance(item, set):
            return {convert(v) for v in item}
        if default_converter is not None:
            return default_converter(item, convert, {})
        return item

    return convert(value)


def fake_module(name: str, **attrs: Any) -> ModuleType:
    fake = ModuleType(name)
    fake.__dict__.update(attrs)
    return fake


def install() -> None:
    # Pure-Python Pyodide modules the SDK uses (relaxed_call, FetchResponse)
    # import the real ffi, so they load before the fake replaces it.
    import pyodide.code
    import pyodide.ffi
    import pyodide.http

    sys.modules["js"] = fake_module(
        "js",
        Object=SimpleNamespace(fromEntries=dict),
        Response=SimpleNamespace(new=js_response),
        Date=SimpleNamespace(new=js_date),
        Error=SimpleNamespace(new=js_error),
    )
    sys.modules["pyodide.ffi"] = fake_module(
        "pyodide.ffi",
        **{
            **vars(pyodide.ffi),
            "to_js": to_js,
            "create_proxy": create_proxy,
            "jsnull": jsnull,
        },
    )
