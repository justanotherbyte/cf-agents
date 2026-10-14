"""Request and response handling shared by the ASGI and WSGI adapters."""

from collections.abc import Iterable
from typing import Any

from .request import Request

# 101/103/204/205/304 must not carry a body per the Fetch spec.
# https://fetch.spec.whatwg.org/#null-body-status
NULL_BODY_STATUSES = frozenset({101, 103, 204, 205, 304})


def request_headers(req: Any) -> Iterable[tuple[str, str]]:
    """The header pairs of a `workers.Request` or a `js.Request`."""
    return req.headers.items() if isinstance(req, Request) else req.headers


def body_encoding(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    """The `Response` init entry for a body the app has already encoded."""
    if any(name.lower() == "content-encoding" for name, _ in headers):
        # The app encoded the body already, as GZipMiddleware does. Without
        # this the runtime encodes it a second time.
        return {"encodeBody": "manual"}
    return {}
