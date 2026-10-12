"""Round-trips the stock SDK's RPC test values between two entrypoints."""

import json
from datetime import datetime

import js
from pyodide.ffi import JsException, JsProxy, to_js

from workers import (
    Request,
    Response,
    WorkerEntrypoint,
)


class Rpc(WorkerEntrypoint):
    async def identity(self, value):
        return value

    async def text_of(self, value):
        return str(value)

    async def caller(self, function):
        return await function()

    async def text(self, response):
        return await response.js_object.text()


class Custom:
    pass


def js_object(value):
    return to_js(value, dict_converter=js.Object.fromEntries)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        failures = []

        async def call(method, *args):
            # The env's Fetcher converts arguments and results both ways.
            return await getattr(self.env.OTHER, method)(*args)

        def check(name, ok):
            if not ok:
                failures.append(name)

        async def raises(name, error, *args):
            try:
                await call("identity", *args)
            except error as caught:
                if error is JsException and "DataCloneError" not in str(caught):
                    failures.append(f"{name}: {caught}")
            except Exception as caught:  # noqa: BLE001
                failures.append(f"{name}: {caught!r}")
            else:
                failures.append(f"{name}: no error")

        for value in ["test", [1, 2, 3], {"key": 42}, 42, 1.2345, False, True, None]:
            received = await call("identity", value)
            check(f"identity {value!r}", received == value)
            check(f"python type {value!r}", not isinstance(received, JsProxy))

        nested = await call("identity", {"a": 1, "b": None, "c": {"d": None}})
        check("nested None", nested == {"a": 1, "b": None, "c": {"d": None}})
        check("attribute access", nested.c.d is None)

        now = datetime.now()
        date = await call("identity", now)
        check("datetime", isinstance(date, datetime) and abs(date - now).seconds < 1)
        check("set", await call("identity", {1, 2, 3}) == {1, 2, 3})
        binary = await call("identity", b"binary")
        check("bytes", isinstance(binary, memoryview) and bytes(binary) == b"binary")

        js_map = js.Map.new(to_js([[1, "one"], [2, "two"]]))
        check("js Map", await call("identity", js_map) == {"1": "one", "2": "two"})
        check("js Set", await call("identity", js.Set.new(to_js([1, 2]))) == {1, 2})
        check("js undefined", await call("identity", js.undefined) is None)
        check("js Date", isinstance(await call("identity", js.Date.new()), datetime))
        error = await call("identity", js.Error.new("message"))
        check("js Error", isinstance(error, Exception))
        # One hop: Error becomes Exception(error.toString()), as in the stock SDK.
        check("js Error text", await call("text_of", js.Error.new("m")) == "Error: m")
        check("js Number", await call("identity", js.Number.new("1234")) == 1234)
        check(
            "js object", await call("identity", js_object({"foo": 42})) == {"foo": 42}
        )

        response = await call("identity", Response("a response"))
        check("Response", isinstance(response, Response))
        check(
            "Response body", await call("text", Response("a response")) == "a response"
        )
        js_request = js.Request.new("https://test.com", js_object({"method": "POST"}))
        request = await call("identity", Request(js_request))
        check("Request", isinstance(request, Request) and request.method == "POST")

        check("callable", await call("caller", lambda: "called") == "called")

        await raises("tuple", TypeError, (1, 2, 3))
        await raises("nested tuple", TypeError, [(1, 2, 3)])
        await raises("tuple in dict", TypeError, {"test": (1, 2, 3)})
        await raises("bytearray", TypeError, bytearray(b"ab"))
        await raises("RegExp", TypeError, js.RegExp.new("ab+c", "i"))
        await raises("RegExp in JS array", TypeError, [js.Array.of(js.RegExp.new("a"))])
        await raises("custom class", JsException, Custom())
        await raises("complex", JsException, complex(1.23))
        await raises("range", JsException, range(0, 30, 5))
        numbers = (n for n in [1, 2])
        await raises("generator", JsException, numbers)
        check("generator not consumed", list(numbers) == [1, 2])

        return Response(json.dumps(failures))
