import asyncio
from datetime import datetime

import pytest
from fake_js import JsValue, js_date, js_error, js_request, jsnull

from workers import Request, Response, python_from_rpc, python_to_rpc

# python_from_rpc


def test_null_and_python_values_arrive_as_is() -> None:
    assert python_from_rpc(jsnull) is None
    for value in ["text", 42, 1.5, True, None, [1, 2], {"a": 1}]:
        assert python_from_rpc(value) == value


def test_js_containers_become_python_containers() -> None:
    received = python_from_rpc(
        JsValue(
            "Object",
            {
                "list": JsValue("Array", [1, jsnull]),
                "set": JsValue("Set", [1, 2]),
                "nested": JsValue("Map", {"none": jsnull}),
            },
        )
    )

    assert received == {"list": [1, None], "set": {1, 2}, "nested": {"none": None}}
    # Dicts also answer attribute access, as the stock SDK's JsDict does.
    assert received.nested.none is None
    with pytest.raises(AttributeError):
        _ = received.missing


def test_js_values_with_python_twins_are_converted() -> None:
    date, error, number, response, request = python_from_rpc(
        JsValue(
            "Array",
            [
                js_date(86_400_000),
                js_error("boom"),
                JsValue("Number", valueOf=lambda: 7),
                JsValue("Response"),
                js_request("https://x/", "POST"),
            ],
        )
    )

    assert date == datetime.fromtimestamp(86_400)
    assert isinstance(error, Exception)
    assert str(error) == "Error: boom"
    assert number == 7
    assert isinstance(response, Response)
    assert isinstance(request, Request)
    assert request.method == "POST"


def test_other_js_values_pass_through() -> None:
    stream = JsValue("ReadableStream")

    assert python_from_rpc(JsValue("Array", [stream])) == [stream]
    assert python_from_rpc(JsValue("TestController")) is None


# python_to_rpc


def test_none_becomes_null_at_any_depth() -> None:
    assert python_to_rpc(None) is jsnull
    assert python_to_rpc({"a": None, "b": [None]}) == {"a": jsnull, "b": [jsnull]}


def test_python_values_with_js_twins_are_converted() -> None:
    def callback() -> None:
        pass

    response = Response("hi")
    sent = python_to_rpc(
        [response, datetime.fromtimestamp(86_400), ValueError("bad"), callback]
    )

    assert sent[0] is response.js_object
    assert sent[1].getTime() == 86_400_000
    assert sent[2].message == "bad"
    assert sent[3].proxied is callback


def test_a_wrapped_js_value_is_sent_as_itself() -> None:
    request = js_request("https://x/")

    assert python_to_rpc(Request(request)) is request


@pytest.mark.parametrize(
    "value",
    [
        (1, 2),
        [(1, 2)],
        {"key": (1, 2)},
        bytearray(b"x"),
        [JsValue("RegExp")],
    ],
    ids=["tuple", "nested tuple", "tuple in dict", "bytearray", "RegExp"],
)
def test_values_rpc_cannot_carry_raise_type_error(value: object) -> None:
    with pytest.raises(TypeError):
        python_to_rpc(value)


def test_unsendable_values_inside_js_or_custom_iterables_raise() -> None:
    class Pairs:
        def __iter__(self) -> object:
            return iter([(1, 2)])

    with pytest.raises(TypeError, match="RegExp cannot be sent over RPC"):
        python_to_rpc([JsValue("Array", [JsValue("RegExp")])])
    with pytest.raises(TypeError, match="tuple"):
        python_to_rpc([Pairs()])


def test_an_awaitable_raises_type_error() -> None:
    async def pending() -> None:
        pass

    coroutine = pending()
    with pytest.raises(TypeError):
        python_to_rpc(coroutine)
    asyncio.run(coroutine)


def test_a_generator_is_not_consumed() -> None:
    # The stock SDK walks every iterable while checking for tuples, which
    # empties a generator before RPC sees it.
    numbers = (n for n in [1, 2])

    python_to_rpc([numbers])

    assert list(numbers) == [1, 2]
