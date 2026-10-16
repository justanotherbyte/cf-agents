import json
from collections.abc import Callable, Coroutine
from functools import partial
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, overload

import js
import pyodide.http
from pyodide.ffi import JsProxy

from ._body import BodyMixin
from ._js import js_body, js_name, none_if_null, python_errors, to_js_headers
from .blob import Blob
from .formdata import FormData

if TYPE_CHECKING:
    from .types import Body, Headers

__all__ = ("RESPONSE_ACCEPTED_TYPES", "FetchResponse", "Response")

RESPONSE_ACCEPTED_TYPES = {
    "Blob",
    "ArrayBuffer",
    "TypedArray",
    "DataView",
    "Uint8Array",
    "Uint8ClampedArray",
    "Int8Array",
    "Uint16Array",
    "Int16Array",
    "Uint32Array",
    "Int32Array",
    "Float16Array",
    "Float32Array",
    "Float64Array",
    "BigInt64Array",
    "BigUint64Array",
    "FormData",
    "ReadableStream",
    "URLSearchParams",
    "Response",
}


class _Json:
    """Response.json(data) is from_json; response.json() parses the body.

    The stock SDK does this inside one method, which a type checker can't
    follow. A descriptor gives each use its own signature.
    """

    @overload
    def __get__(self, instance: None, owner: type) -> Callable[..., "Response"]: ...
    @overload
    def __get__(
        self, instance: "FetchResponse", owner: type
    ) -> Callable[..., Coroutine[Any, Any, Any]]: ...
    def __get__(self, instance: "FetchResponse | None", owner: type) -> Any:
        if instance is None:
            return _stock_json
        return partial(pyodide.http.FetchResponse.json, instance)


def _stock_json(data: Any, *args: Any, **kwargs: Any) -> Any:
    if isinstance(data, Response):
        return pyodide.http.FetchResponse.json(data)
    return FetchResponse.from_json(data, *args, **kwargs)


class FetchResponse(BodyMixin, pyodide.http.FetchResponse):
    @property
    def body(self) -> "js.ReadableStream | None":
        return none_if_null(self.js_object.body)

    @property
    def js_object(self) -> "js.Response":
        return self.js_response

    async def formData(self) -> FormData:  # noqa: N802 (the stock SDK's alias)
        return await self.form_data()

    def replace_body(self, body: "Body") -> "Response":
        """A new Response with this one's status and headers and a new body."""
        with js_body(body) as converted:
            return Response(js.Response.new(converted, self.js_response))

    @staticmethod
    def redirect(url: str, status: HTTPStatus | int = HTTPStatus.FOUND) -> "Response":
        with python_errors():
            return Response(js.Response.redirect(url, int(status)))

    @staticmethod
    def from_json(
        data: Any,
        status: HTTPStatus | int = HTTPStatus.OK,
        status_text: str = "",
        headers: "Headers | None" = None,
    ) -> "Response":
        options = Response._create_options(status, status_text, headers)

        with python_errors():
            if isinstance(data, JsProxy):
                return Response(js.Response.json(data, **options))

            if "headers" not in options:
                options["headers"] = to_js_headers({"content-type": "application/json"})
            elif not options["headers"].has("content-type"):
                options["headers"].set("content-type", "application/json")

            return Response(js.Response.new(json.dumps(data), **options))

    # A descriptor where Pyodide has a method, so ty reports the override.
    json = _Json()  # ty: ignore[invalid-attribute-override]


class Response(FetchResponse):
    """An HTTP response, with an API like the web's Response."""

    def __init__(
        self,
        body: "Body" = None,
        status: HTTPStatus | int | None = None,
        status_text: str = "",
        headers: "Headers | None" = None,
        web_socket: "js.WebSocket | None" = None,
    ) -> None:
        name = js_name(body)
        if name is not None:
            if name not in RESPONSE_ACCEPTED_TYPES:
                raise TypeError(f"Unsupported type in Response: {name}")
        elif not isinstance(body, (str, bytes, FormData, Blob)) and body is not None:
            raise TypeError(f"Unsupported type in Response: {type(body).__name__}")

        if name == "Response":
            if status is not None or status_text or headers is not None:
                raise ValueError(
                    "Expected no options when constructing Response from a js.Response"
                )
            js_response: Any = body
            super().__init__(js_response.url, js_response)
            return

        options = self._create_options(status, status_text, headers, web_socket)
        with js_body(body) as converted:
            js_response = js.Response.new(converted, **options)
        super().__init__(js_response.url, js_response)

    def __repr__(self) -> str:
        parts = [f"status={self.status}"]
        if self.js_object.statusText:
            parts.append(f"status_text={self.status_text!r}")
        if "content-type" in self.headers:
            parts.append(f"content_type={self.headers['content-type']!r}")
        if self.js_object.url:
            parts.append(f"url={self.js_object.url!r}")
        if self.js_object.type != "default":
            parts.append(f"type={self.js_object.type!r}")

        return f"Response({', '.join(parts)})"

    @staticmethod
    def _create_options(
        status: HTTPStatus | int | None = HTTPStatus.OK,
        status_text: str = "",
        headers: "Headers | None" = None,
        web_socket: "js.WebSocket | None" = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if status:
            options["status"] = int(status)
        if status_text:
            options["statusText"] = status_text
        if headers:
            options["headers"] = to_js_headers(headers)
        if web_socket:
            options["webSocket"] = web_socket

        return options
