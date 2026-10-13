import json
from http import HTTPMethod
from typing import TYPE_CHECKING, Any, Unpack

import js

from ._body import BodyMixin
from ._js import js_body, js_name, none_if_null, to_js_headers

if TYPE_CHECKING:
    from .types import FetchKwargs

__all__ = ["Request"]


class Request(BodyMixin):
    def __init__(
        self, input: "Request | str | js.Request", **options: "Unpack[FetchKwargs]"
    ) -> None:
        if js_name(input) == "Request":
            if options:
                raise ValueError(
                    "Expected no options when constructing Request from a js.Request"
                )
            self._js_request: Any = input
            return
        js_options: dict[str, Any] = dict(options)
        if isinstance(js_options.get("method"), HTTPMethod):
            js_options["method"] = js_options["method"].value
        if "headers" in js_options:
            js_options["headers"] = to_js_headers(js_options["headers"])
        body = js_options.pop("body", None)
        with js_body(body) as converted:
            if body is not None:
                js_options["body"] = converted
            self._js_request = js.Request.new(
                input.js_object if isinstance(input, Request) else input,
                **js_options,
            )

    def __repr__(self) -> str:
        return (
            f"Request(method={self._js_request.method!r}, url={self._js_request.url!r})"
        )

    @property
    def js_object(self) -> "js.Request":
        return self._js_request

    @property
    def body(self) -> "js.ReadableStream":
        return self.js_object.body

    @property
    def body_used(self) -> bool:
        return self.js_object.bodyUsed

    @property
    def cf(self) -> Any:
        """Cloudflare's properties of an incoming request, or None if absent."""
        return none_if_null(self.js_object.cf)

    @property
    def cache(self) -> str:
        return self.js_object.cache

    @property
    def credentials(self) -> str:
        return self.js_object.credentials

    @property
    def destination(self) -> str:
        return self.js_object.destination

    @property
    def integrity(self) -> str:
        return self.js_object.integrity

    @property
    def is_history_navigation(self) -> bool:
        return self.js_object.isHistoryNavigation

    @property
    def keepalive(self) -> bool:
        return self.js_object.keepalive

    @property
    def method(self) -> HTTPMethod | str:
        # HTTPMethod for a standard method. The stock SDK raises KeyError on
        # any other; this returns the method string instead.
        method = self.js_object.method
        return HTTPMethod[method] if method in HTTPMethod.__members__ else method

    @property
    def mode(self) -> str:
        return self.js_object.mode

    @property
    def redirect(self) -> str:
        return self.js_object.redirect

    @property
    def referrer(self) -> str:
        return self.js_object.referrer

    @property
    def referrer_policy(self) -> str:
        return self.js_object.referrerPolicy

    @property
    def url(self) -> str:
        return self.js_object.url

    def _raise_if_failed(self) -> None:
        if self.body_used:
            raise OSError("Body already used")

    async def buffer(self) -> "js.ArrayBuffer":
        self._raise_if_failed()
        return await self.js_object.arrayBuffer()

    async def bytes(self) -> bytes:
        self._raise_if_failed()
        return (await self.buffer()).to_bytes()

    def clone(self) -> "Request":
        self._raise_if_failed()
        return Request(self.js_object.clone())

    async def json(self, **kwargs: Any) -> Any:
        self._raise_if_failed()
        return json.loads(await self.text(), **kwargs)

    async def text(self) -> str:
        self._raise_if_failed()
        return await self.js_object.text()
