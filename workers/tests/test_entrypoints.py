import asyncio
from inspect import isawaitable
from typing import Any

import pytest
from fake_js import FakeJsRequest, FakeJsResponse
from pyodide.code import relaxed_call

from workers import (
    DurableObject,
    Request,
    Response,
    WorkerEntrypoint,
    python_from_rpc,
    python_to_rpc,
)


def call(instance: Any, method: str, *args: Any) -> Any:
    """Call a method the way workerd's introspection.wrapper_func does."""

    async def wrapper_func() -> Any:
        py_args = [python_from_rpc(arg) for arg in args]
        # workerd calls fetch relaxed, and on legacy dates with env and ctx too.
        if method in ("fetch", "on_fetch"):
            result = relaxed_call(getattr(instance, method), *py_args, "env", "ctx")
        else:
            result = getattr(instance, method)(*py_args)
        return python_to_rpc(await result if isawaitable(result) else result)

    return asyncio.run(wrapper_func())


def test_fetch_returns_a_js_response() -> None:
    class Default(WorkerEntrypoint):
        async def fetch(self, request: Request) -> Response:
            return Response(f"{self.env} {request.method} {request.url}", status=201)

    js_response = call(
        Default("ctx", "env"), "fetch", FakeJsRequest("https://x/", "POST")
    )

    assert isinstance(js_response, FakeJsResponse)
    assert js_response.body == "env POST https://x/"
    assert js_response.init == {"status": 201}


def test_fetch_receives_the_js_request_wrapped() -> None:
    js_request = FakeJsRequest("https://x/")
    seen = []

    class Default(WorkerEntrypoint):
        async def fetch(self, request: Request) -> Response:
            seen.append(request.js_object)
            return Response()

    call(Default(None, None), "fetch", js_request)

    assert seen == [js_request]


LEGACY_NAMES = [
    (WorkerEntrypoint, "fetch"),
    (WorkerEntrypoint, "connect"),
    (DurableObject, "fetch"),
    (DurableObject, "connect"),
    (DurableObject, "alarm"),
    (DurableObject, "webSocketMessage"),
    (DurableObject, "webSocketClose"),
    (DurableObject, "webSocketError"),
]


@pytest.mark.parametrize(("base", "name"), LEGACY_NAMES)
def test_legacy_dates_call_the_short_name(base: type, name: str) -> None:
    async def handler(self: Any, *args: Any) -> Any:
        return args

    cls = type("Handler", (base,), {name: handler})
    on_name = getattr(cls(None, None), f"on_{name}")

    if name == "fetch":
        assert call(cls(None, None), "on_fetch", "r") == ("r", "env", "ctx")
    else:
        # workerd calls the other handlers directly, without wrapper_func.
        assert asyncio.run(on_name(1, 2)) == (1, 2)


def test_the_alias_finds_a_handler_from_a_mixin() -> None:
    class Mixin:
        async def fetch(self, request: Request) -> Response:
            return Response("mixin")

    class Default(Mixin, WorkerEntrypoint):
        pass

    js_response = call(Default(None, None), "on_fetch", FakeJsRequest("/"))

    assert js_response.body == "mixin"


def test_a_defined_on_name_is_kept() -> None:
    class Default(WorkerEntrypoint):
        async def fetch(self, request: Request) -> Response:
            return Response("fetch")

        async def on_fetch(self, request: Request) -> Response:
            return Response("on_fetch")

    js_response = call(Default(None, None), "on_fetch", FakeJsRequest("/"))

    assert js_response.body == "on_fetch"


def test_the_alias_follows_a_subclass_override() -> None:
    class Base(WorkerEntrypoint):
        async def fetch(self, request: Request) -> Response:
            return Response("base")

    class Child(Base):
        async def fetch(self, request: Request) -> Response:
            return Response("child")

    js_response = call(Child(None, None), "on_fetch", FakeJsRequest("/"))

    assert js_response.body == "child"
