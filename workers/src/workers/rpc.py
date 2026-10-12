import datetime
import inspect
from typing import Any

import js
from pyodide.ffi import create_proxy, to_js

from ._js import js_name, jsnull
from .blob import Blob, File
from .formdata import FormData
from .request import Request
from .response import Response


def python_from_rpc(obj: Any) -> Any:
    """Convert a value workerd received over RPC to Python, as the stock SDK does."""
    if obj is jsnull:
        return None
    name = js_name(obj)
    if name is None:
        return obj
    if name == "TestController":
        return None
    return _null_to_none(obj.to_py(default_converter=_default_from_js))


def python_to_rpc(value: Any) -> Any:
    """Convert a Python value to send over RPC, as the stock SDK does."""
    value = _none_to_null(value)
    if value is jsnull:
        return value
    # to_js converts some containers without asking the default converter.
    _refuse_unsendable(value)
    return to_js(
        value, default_converter=_default_to_js, dict_converter=js.Object.fromEntries
    )


class JsDict(dict):
    """A dict received over RPC, whose keys also read as attributes."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


_PYTHON_TWINS = {
    "Response": Response,
    "Request": Request,
    "FormData": FormData,
    "Blob": Blob,
    "File": File,
    "Date": lambda date: datetime.datetime.fromtimestamp(date.getTime() / 1000),
    "Error": lambda error: Exception(error.toString()),
    "Number": lambda number: number.valueOf(),
}


def _default_from_js(value: Any, converter: Any, cache: Any) -> Any:
    if value is jsnull:
        return None
    twin = _PYTHON_TWINS.get(js_name(value) or "")
    return twin(value) if twin else value


def _null_to_none(value: Any) -> Any:
    if value is jsnull:
        return None
    if isinstance(value, dict):
        return JsDict({k: _null_to_none(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_null_to_none(v) for v in value]
    return value


def _none_to_null(value: Any) -> Any:
    if value is None:
        return jsnull
    if isinstance(value, dict):
        return {k: _none_to_null(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_none_to_null(v) for v in value]
    return value


def _refuse_unsendable(value: Any) -> None:
    # A wrapper is sent as its JS object, and callables as proxies.
    if hasattr(value, "js_object") or (callable(value) and not isinstance(value, type)):
        return
    if js_name(value) == "RegExp":
        raise TypeError(f"{value.constructor.name} cannot be sent over RPC.")
    if isinstance(value, (tuple, bytearray)):
        raise TypeError(f"{type(value)} cannot be sent over RPC.")
    if inspect.isawaitable(value):
        raise TypeError(f"Awaitable {type(value)} cannot be sent over RPC.")
    if isinstance(value, (str, bytes)):
        return
    try:
        items = iter(value.values() if isinstance(value, dict) else value)
    except TypeError:
        return
    # Iterating an iterator such as a generator would empty it before it is
    # sent, which the stock SDK does. A container gives a fresh iterator.
    if items is value:
        return
    for item in items:
        _refuse_unsendable(item)


def _default_to_js(value: Any, converter: Any, cache: Any) -> Any:
    if hasattr(value, "js_object"):
        return value.js_object
    if isinstance(value, datetime.datetime):
        return js.Date.new(value.timestamp() * 1000)
    if isinstance(value, Exception):
        return js.Error.new(str(value))
    if callable(value) and not isinstance(value, type):
        # A persistent proxy, so the receiver can call it after this returns.
        return create_proxy(value)
    _refuse_unsendable(value)
    return value
