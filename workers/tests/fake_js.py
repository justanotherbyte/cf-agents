"""A fake JS layer, so the SDK imports and runs on the host."""

import sys
from types import ModuleType, SimpleNamespace
from typing import Any


class FakeJsRequest:
    constructor = SimpleNamespace(name="Request")

    def __init__(self, url: str, method: str = "GET") -> None:
        self.url = url
        self.method = method


class FakeJsResponse:
    def __init__(self, body: Any, init: Any) -> None:
        self.body = body
        self.init = init

    @classmethod
    def new(cls, body: Any = None, init: Any = None) -> "FakeJsResponse":
        return cls(body, init)


def to_js(value: Any, dict_converter: Any = None) -> Any:
    if isinstance(value, dict) and dict_converter is not None:
        return dict_converter(value.items())
    return value


def fake_module(name: str, **attrs: Any) -> ModuleType:
    fake = ModuleType(name)
    fake.__dict__.update(attrs)
    return fake


def install() -> None:
    # The real pyodide.code (relaxed_call is pure Python) needs the real ffi.
    import pyodide.code  # noqa: F401

    sys.modules["js"] = fake_module(
        "js", Object=SimpleNamespace(fromEntries=dict), Response=FakeJsResponse
    )
    sys.modules["pyodide.ffi"] = fake_module("pyodide.ffi", to_js=to_js)
