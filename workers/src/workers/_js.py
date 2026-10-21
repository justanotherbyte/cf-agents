"""Helpers for crossing between Python and JS values."""

import http.client
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any

import js
from pyodide.ffi import JsException, create_proxy, destroy_proxies

try:
    # Pyodide 0.28 added jsnull. The pyodide-py stubs for 3.12 (0.27.8) lack it.
    from pyodide.ffi import jsnull  # ty: ignore[unresolved-import]
except ImportError:
    # Pyodide 0.26 (the 3.12 target) has no jsnull, so None crosses as undefined.
    jsnull = None

try:
    import _cloudflare_compat_flags
except ImportError:  # outside workerd
    _cloudflare_compat_flags = None


def js_name(value: Any) -> str | None:
    """The JS constructor name of a JS value, or None for a Python value."""
    return value.constructor.name if hasattr(value, "constructor") else None


def none_if_null(value: Any) -> Any:
    return None if value is jsnull else value


def compat_flag(name: str) -> bool:
    return bool(getattr(_cloudflare_compat_flags, name, False))


@contextmanager
def js_body(body: Any) -> Generator[Any]:
    """The JS value for a request or response body, valid inside the block."""
    if isinstance(body, bytes):
        with lend_buffer(body) as view:
            yield view
    elif hasattr(body, "js_object"):
        yield body.js_object
    else:
        yield body


@contextmanager
def lend_buffer(value: Any) -> Generator[Any]:
    """A JS view of a Python buffer, without a copy, valid inside the block."""
    proxy = create_proxy(value)
    buffer = proxy.getBuffer()

    try:
        yield buffer.data
    finally:
        buffer.release()
        proxy.destroy()


@contextmanager
def managed_pyproxies() -> Generator[Any]:
    proxies = js.Array.new()
    try:
        yield proxies
    finally:
        destroy_proxies(proxies)


_PYTHON_ERRORS = {"RangeError": ValueError, "TypeError": TypeError}


@contextmanager
def python_errors() -> Generator[None]:
    """Raise a JS RangeError or TypeError from the block as its Python twin."""
    try:
        yield
    except JsException as error:
        python_error = _PYTHON_ERRORS.get(error.name)
        if python_error is None:
            raise
        raise python_error(error.message) from error


def to_js_headers(headers: Any) -> Any:
    if isinstance(headers, list):
        return js.Headers.new(headers)
    if isinstance(headers, dict):
        return js.Headers.new(headers.items())
    if js_name(headers) == "Headers":
        return headers
    raise TypeError("Received unexpected type for headers argument")


# The stock SDK's type: an HTTPMessage that is also a Mapping. The two bases
# type keys(), get() and the rest differently, which ty reports.
class HTTPMessageMapping(http.client.HTTPMessage, Mapping):  # ty: ignore[invalid-method-override]
    pass


def headers_to_message(js_headers: Any) -> HTTPMessageMapping:
    # Newer Pyodide versions already expose headers as an HTTPMessage.
    if isinstance(js_headers, HTTPMessageMapping):
        return js_headers

    result = HTTPMessageMapping()
    if not compat_flag("python_request_headers_preserve_commas"):
        for key, value in js_headers:
            result[key] = value.strip()
        return result

    # JS joins duplicate headers with commas, except Set-Cookie.
    for value in js_headers.getSetCookie() or ():
        result.add_header("Set-Cookie", value.strip())
    for key, value in js_headers:
        if key.lower() != "set-cookie":
            result.add_header(key, value.strip())

    return result
