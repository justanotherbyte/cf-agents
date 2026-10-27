"""The HTTP transport the client sends every request through.

`HttpTransport` is the seam: the default `FetchTransport` uses the Workers
``fetch`` (aborting on cancellation), and tests pass a transport that runs
an ASGI app in-process (``.design/mcp_engine.md`` §3).
"""

from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from .. import _ffi
from .errors import MCPProtocolError, MCPTransportError

__all__ = (
    "FetchTransport",
    "HttpRequest",
    "HttpResponse",
    "HttpTransport",
    "read_body",
    "send_within_origin",
)

_MAX_REDIRECTS = 3
_MAX_BODY_SIZE = 1024 * 1024  # bytes; as one SSE event


@dataclass(slots=True, kw_only=True)
class HttpRequest:
    """One outbound request.

    Parameters
    ----------
    method
        The HTTP method.
    url
        The absolute URL.
    headers
        The request headers.
    body
        The body, if any.
    """

    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: str | None = None


class HttpResponse(Protocol):
    """A response whose body is read as it arrives."""

    @property
    def status(self) -> int:
        """``int``: the HTTP status."""
        ...

    @property
    def headers(self) -> Mapping[str, str]:
        """``Mapping[str, str]``: the headers, with lower-case names."""
        ...

    def chunks(self) -> AsyncIterator[bytes]:
        """Yield the body's chunks (read once)."""
        ...


class HttpTransport(Protocol):
    """Sends requests. Leaving the context ends the request, read or not."""

    def request(
        self, request: HttpRequest
    ) -> AbstractAsyncContextManager[HttpResponse]:
        """Send ``request`` and enter its response.

        Raises
        ------
        MCPTransportError
            If the request fails before a response arrives, or the body read
            does.
        """
        ...


class FetchTransport:
    """The default transport: the Workers ``fetch``."""

    __slots__ = ()

    @asynccontextmanager
    async def request(self, request: HttpRequest) -> AsyncGenerator[HttpResponse]:
        """Send ``request`` with ``fetch`` and enter its response.

        Raises
        ------
        MCPTransportError
            If the request or the body read fails.
        """
        try:
            async with _ffi.open_fetch(
                request.method,
                request.url,
                headers=request.headers,
                body=request.body,
            ) as response:
                yield response
        except _ffi.JsException as error:
            raise MCPTransportError(
                f"{request.method} {request.url}: {error}"
            ) from error


async def read_body(response: HttpResponse) -> str:
    """Return the whole body as text (UTF-8, errors replaced).

    Raises
    ------
    MCPProtocolError
        If the body is larger than 1 MiB.
    """
    parts: list[bytes] = []
    size = 0
    async for chunk in response.chunks():
        size += len(chunk)
        if size > _MAX_BODY_SIZE:
            raise MCPProtocolError("Response body is larger than 1 MiB")
        parts.append(chunk)
    return b"".join(parts).decode("utf-8", errors="replace")


@asynccontextmanager
async def send_within_origin(
    http: HttpTransport, request: HttpRequest
) -> AsyncGenerator[HttpResponse]:
    """Send ``request``, following 307/308 redirects that stay on its origin.

    Everything on a request (headers, auth, body) was meant for one endpoint,
    so only a same-origin 307 or 308, such as a trailing-slash fix, is
    followed, at most 3 times. Any other redirect response is returned for
    the caller to treat as a failure (the SDK's ``stream_within_origin``).

    Raises
    ------
    MCPTransportError
        If a request fails.
    """
    for _ in range(_MAX_REDIRECTS):
        async with http.request(request) as response:
            location = _same_origin_redirect(request.url, response)
            if location is None:
                yield response
                return
        request = replace(request, url=location)
    async with http.request(request) as response:
        yield response


def _same_origin_redirect(url: str, response: HttpResponse) -> str | None:
    location = response.headers.get("location")
    if response.status not in (307, 308) or not location:
        return None
    target = urljoin(url, location)
    source, destination = urlsplit(url), urlsplit(target)
    if (source.scheme, source.netloc) != (destination.scheme, destination.netloc):
        return None
    return target
