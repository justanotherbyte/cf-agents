from collections.abc import Awaitable
from http import HTTPMethod
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

if TYPE_CHECKING:
    import js
    from pyodide.ffi import JsBuffer

    from .blob import Blob
    from .formdata import FormData

__all__ = [
    "Body",
    "Context",
    "FetchKwargs",
    "Headers",
    "JSBody",
    "RequestInitCfProperties",
]


class Context(Protocol):
    def waitUntil(self, other: Awaitable[Any]) -> None: ...  # noqa: N802


type JSBody = js.Blob | JsBuffer | js.FormData | js.ReadableStream | js.URLSearchParams
type Body = str | bytes | FormData | Blob | JSBody | None
type Headers = dict[str, str] | list[tuple[str, str]] | js.Headers


class RequestInitCfProperties(TypedDict, total=False):
    """https://developers.cloudflare.com/workers/runtime-apis/request/#the-cf-property-requestinitcfproperties"""

    apps: bool | None
    cacheEverything: bool | None
    cacheKey: str | None
    cacheTags: list[str] | None
    cacheTtl: int
    cacheTtlByStatus: dict[str, int]
    image: Any | None
    mirage: bool | None
    polish: str | None
    resolveOverride: str | None
    scrapeShield: bool | None
    webp: bool | None


class FetchKwargs(TypedDict, total=False):
    """https://developers.cloudflare.com/workers/runtime-apis/request/#options"""

    headers: "Headers | None"
    body: "Body | None"
    method: HTTPMethod | str | None
    redirect: str | None
    cf: RequestInitCfProperties | None
    # A JS fetch function, such as a service binding's.
    fetcher: Any
