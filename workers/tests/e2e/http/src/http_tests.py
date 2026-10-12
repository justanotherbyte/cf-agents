"""The stock SDK's HTTP unit tests (tests/workerd-test/sdk/tests/test_sdk.py),
minus those needing env, plus a test for each stock bug crosswind fixes."""

from contextlib import contextmanager
from http import HTTPMethod, HTTPStatus

import js
from pyodide.ffi import JsProxy, to_js

from workers import (
    Blob,
    File,
    FormData,
    Request,
    Response,
    fetch,
    python_from_rpc,
)

ORIGIN: str = ""


@contextmanager
def raises(error, message=None):
    try:
        yield
    except error as caught:
        if message is not None:
            assert str(caught) == message, str(caught)
    else:
        raise AssertionError(f"expected {error.__name__}")


# Stock: fetch


async def test_fetch_returns_a_response():
    response = await fetch(f"{ORIGIN}/sub")
    assert isinstance(response, Response)
    assert await response.text() == "Hi there!"


async def test_can_modify_a_fetched_response():
    fetched = await fetch(f"{ORIGIN}/sub")
    response = Response(fetched.body, status=201, headers={"X-Mod": "modified"})
    assert await response.text() == "Hi there!"
    assert response.status == 201
    assert response.headers.get("X-Mod") == "modified"


async def test_duplicate_headers_from_tuples():
    response = Response("x", headers=[("X-Dup", "modified"), ("X-Dup", "another")])
    assert response.headers.get("X-Dup") == "modified, another"


async def test_fetch_options():
    response = await fetch(
        f"{ORIGIN}/sub",
        method=HTTPMethod.GET,
        body=None,
        headers={"Custom-Req-Header": "123"},
    )
    assert await response.text() == "Hi there!"
    assert response.headers.get("Custom-Header-That-Should-Passthrough") == "true"


async def test_fetch_rejects_a_js_style_options_dict():
    with raises(TypeError):
        # The JS calling convention, which Python rejects.
        await fetch(f"{ORIGIN}/sub", {"headers": {"X": "1"}})  # ty: ignore[too-many-positional-arguments]


async def test_fetch_manual_redirect_and_cf_options():
    response = await fetch(
        f"{ORIGIN}/redirect",
        redirect="manual",
        cf={"cacheTtl": 5, "cacheEverything": True, "cacheKey": "key"},
    )
    assert response.status == 301


async def test_fetched_form_data():
    data = await (await fetch(f"{ORIGIN}/formdata")).formData()
    assert data["field"] == "value"


async def test_fetched_form_data_with_blobs():
    data = await (await fetch(f"{ORIGIN}/formdatablob")).form_data()
    assert data["field"] == "value"
    assert await data["blob.py"].text() == "print(42)"
    assert data["blob.py"].content_type == "text/python"
    assert data["metadata"].name == "metadata.json"


# Stock: Response


async def test_inherited_response_methods():
    assert await Response("test123").text() == "test123"


async def test_redirect_rejects_invalid_input():
    with raises(ValueError):
        Response.redirect("", HTTPStatus.BAD_GATEWAY)


async def test_response_json():
    response = Response.json({"obj": {"field": 123}}, status=HTTPStatus.NOT_FOUND)
    assert await response.text() == '{"obj": {"field": 123}}'
    assert response.status == 404


async def test_replace_body():
    response = Response("test", status=201, status_text="Created")
    cloned = response.replace_body("other")
    assert cloned.status == 201
    assert cloned.status_text == "Created"
    assert await cloned.text() == "other"


async def test_response_unit_tests():
    response = Response.json([1, 2, 3])
    assert await response.text() == "[1, 2, 3]"
    assert (
        repr(response)
        == "Response(status=200, status_text='OK', content_type='application/json')"
    )
    assert await Response.from_json([1, 2, 3]).text() == "[1, 2, 3]"
    assert await Response("[1, 2, 3]").json() == [1, 2, 3]
    # Stock quirk: called on the class with a Response, json parses its body.
    # The class-level type describes from_json, the common use.
    parsed = Response.json(Response("[4]"))
    assert await parsed == [4]  # ty: ignore[invalid-await]
    assert await Response.json("test").text() == '"test"'
    assert await Response.json({"field": 42}).text() == '{"field": 42}'
    assert Response.json("test").headers.get("content-type") == "application/json"
    other = Response.json("test", headers={"x-other-header": "42"})
    assert other.headers.get("content-type") == "application/json"
    custom = Response.json("test", headers={"Content-Type": "42"})
    assert custom.headers.get("content-type") == "42"
    empty = Response(None, status=204)
    assert empty.status == 204
    assert empty.body is None
    assert await Response(b"test").text() == "test"

    class Test:
        pass

    with raises(TypeError, "Object of type Test is not JSON serializable"):
        Response.json(Test())
    with raises(TypeError, "Object of type set is not JSON serializable"):
        Response.json({1, 2, 3})
    with raises(TypeError, "Unsupported type in Response: Test"):
        Response(Test())
    socket = js.WebSocket.new("ws://example.com/ignore")
    assert Response(None, status=101, web_socket=socket).status == 101


async def test_response_buffer_sources():
    cases = [
        js.Uint8Array.new(to_js([1, 2, 3, 4, 5, 6, 7, 8])).buffer,
        js.DataView.new(js.Uint8Array.new(to_js([9, 10, 11, 12])).buffer),
        js.Uint8Array.new(to_js([1, 2, 3, 4])),
        js.Int16Array.new(to_js([1, -2, 3, -4])),
        js.Float64Array.new(to_js([1.5, -2.5])),
        js.Uint8Array.new(to_js([0, 1, 2, 3, 4, 5])).subarray(1),
    ]
    for body in cases:
        buffer = await Response(body).buffer()
        assert len(buffer.to_bytes()) == int(body.byteLength)


# Stock: FormData, Blob, File, Request


async def test_form_data():
    js_data = js.FormData.new()
    js_data.append("foobar", 123)
    js_data.append("key", "lorem ipsum")
    js_data.append("key", "dolor sit amet")
    data = FormData(js_data)
    assert data["foobar"] == "123"
    assert data["key"] == "lorem ipsum"
    assert data.get_all("key") == ["lorem ipsum", "dolor sit amet"]
    assert "unknown key" not in data
    data = FormData({"key": "foobar"})
    data.append("key", "another")
    data.append("key2", "foobar2")
    assert ("key", "another") in set(data.items())
    assert len(list(data.keys())) == 3
    assert "foobar2" in set(data.values())


async def test_blob():
    data = FormData()
    data["test"] = Blob(["some string"])
    assert await data["test"].text() == "some string"
    data["js"] = js.Blob.new(to_js(["another string"]))
    assert isinstance(data["js"], Blob)
    typed = Blob(["test"], content_type="application/json")
    assert typed.js_object.type == "application/json"
    assert typed.size == 4
    assert await typed.bytes() == b"test"
    assert await typed.slice(1, 3).text() == "es"
    assert await Blob([memoryview(b"foobar")]).text() == "foobar"
    assert Blob(typed).content_type == "application/json"
    assert Blob(typed, content_type="other/type").content_type == "other/type"
    assert Blob([typed]).content_type == ""
    file = File("my file", filename="test.txt", content_type="text/plain")
    assert file.content_type == "text/plain"
    assert file.name == "test.txt"


async def test_request():
    request = Request("https://test.com", method=HTTPMethod.POST)
    assert request.method == HTTPMethod.POST
    assert repr(request) == "Request(method='POST', url='https://test.com/')"
    js_headers = js.Headers.new()
    js_headers.set("foo", "bar")
    assert Request("http://example.com", headers=js_headers).headers["foo"] == "bar"
    assert Request("http://x.com", headers={"aaaa": "t"}).headers["aaaa"] == "t"
    used = Request("http://example.com", body='{"field": 42}', method="POST")
    assert (await used.json())["field"] == 42
    with raises(OSError):
        used.clone()
    js_headers = js.Headers.new()
    js_headers.append("Accept-encoding", "deflate")
    js_headers.append("Accept-encoding", "gzip")
    duplicated = Request("http://example.com", headers=js_headers)
    assert duplicated.url == "http://example.com/"
    assert duplicated.headers.get_all("Accept-encoding") == ["deflate, gzip"]
    commas = Request("http://x.com", headers={"User-Agent": "Example, Agent/1.0"})
    assert commas.headers.get_all("User-Agent") == ["Example, Agent/1.0"]
    js_headers = js.Headers.new()
    js_headers.append("Set-Cookie", "a=b, c=d")
    js_headers.append("Set-Cookie", "e=f")
    cookies = Request("http://example.com", headers=js_headers)
    assert cookies.headers.get_all("Set-Cookie") == ["a=b, c=d", "e=f"]
    blob_request = Request("http://example.com", body="foobar", method="POST")
    assert await (await blob_request.blob()).text() == "foobar"
    binary = bytes(range(256))
    assert await Request("http://x.com", body=binary, method="POST").bytes() == binary
    js_form = js.FormData.new()
    js_form.append("foobar", 123)
    posted = Request("http://example.com", body=js_form, method="POST")
    assert (await posted.form_data())["foobar"] == "123"


async def test_request_cf():
    assert Request("https://test.com").cf is None
    options = to_js({"cf": {"colo": "DFW"}}, dict_converter=js.Object.fromEntries)
    cf = Request(js.Request.new("https://test.com", options)).cf
    assert isinstance(cf, JsProxy)
    assert cf.colo == "DFW"


# crosswind fixes for stock bugs


async def test_fix_file_received_over_rpc():
    file = python_from_rpc(js.File.new(to_js(["x"]), "a.txt", type="text/plain"))
    assert isinstance(file, File)
    assert file.name == "a.txt"
    assert file.content_type == "text/plain"


async def test_fix_form_data_len_and_missing_key():
    data = FormData({"a": "1"})
    data.append("a", "2")
    data["b"] = "3"
    assert len(data) == 3
    with raises(KeyError):
        data["missing"]
    assert data.get("missing") is None


async def test_fix_response_from_blob():
    assert await Response(Blob(["blob body"])).text() == "blob body"


async def test_fix_non_standard_method():
    assert Request("http://example.com", method="PURGE").method == "PURGE"


async def test_fix_buffers_as_a_single_blob_part():
    assert await Blob(memoryview(b"abc")).text() == "abc"
    assert await Blob(bytearray(b"abc")).text() == "abc"


async def test_fix_redirect_returns_a_response():
    response = Response.redirect("https://example.com/", 301)
    assert isinstance(response, Response)
    assert response.status == 301
    assert response.headers.get("location") == "https://example.com/"
