"""The HTTP wrappers' pure-Python branches. tests/e2e/http runs the rest in
workerd, where Headers, Blob and FormData are real."""

from http import HTTPMethod
from types import SimpleNamespace

import js
import pytest
from fake_js import JsValue, js_request, js_response

from workers import Blob, Request, Response


def test_response_rejects_unsupported_bodies() -> None:
    with pytest.raises(TypeError, match="Unsupported type in Response: object"):
        Response(object())
    with pytest.raises(TypeError, match="Unsupported type in Response: Map"):
        Response(JsValue("Map"))


def test_wrapping_a_js_object_takes_no_options() -> None:
    with pytest.raises(ValueError, match="Expected no options"):
        Response(js_response(), status=201)
    with pytest.raises(ValueError, match="Expected no options"):
        Request(js_request("https://x/"), method="POST")


def test_method_is_an_http_method_or_the_raw_string() -> None:
    assert Request(js_request("https://x/", "POST")).method is HTTPMethod.POST
    # The stock SDK raises KeyError for a method HTTPMethod lacks.
    assert Request(js_request("https://x/", "PURGE")).method == "PURGE"


def test_headers_are_stripped_without_the_commas_flag() -> None:
    request = JsValue("Request", headers=[("X-Spaced", "  value  ")])

    assert Request(request).headers["x-spaced"] == "value"


def test_blob_takes_a_js_typed_array_as_one_part(monkeypatch) -> None:
    monkeypatch.setattr(js, "Array", SimpleNamespace(new=list), raising=False)
    monkeypatch.setattr(
        js, "Blob", SimpleNamespace(new=lambda parts, **_: parts), raising=False
    )
    array = JsValue("Uint8Array", [104, 105], to_memoryview=memoryview)
    assert Blob(array).js_object == [array]
