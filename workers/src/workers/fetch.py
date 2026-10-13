from http import HTTPMethod
from typing import TYPE_CHECKING, Any, Unpack

import js
from pyodide import __version__ as pyodide_version
from pyodide.ffi import JsException, to_js
from pyodide.http import pyfetch

from .request import Request
from .response import Response

if TYPE_CHECKING:
    from .types import FetchKwargs

__all__ = ["fetch"]


async def _pyfetch_0_26(request: Any, **kwargs: Any) -> Response:
    # Pyodide 0.26.0a2's pyfetch lacks `fetcher`. Copied from Pyodide d3f99e1d.
    custom_fetch = kwargs.pop("fetcher", None) or js.fetch
    try:
        return Response(
            await custom_fetch(
                request, to_js(kwargs, dict_converter=js.Object.fromEntries)
            )
        )
    except JsException as error:
        raise OSError(error.message) from None


async def fetch(
    resource: "str | Request | js.Request", **options: "Unpack[FetchKwargs]"
) -> Response:
    if isinstance(resource, Request):
        resource = resource.js_object
    kwargs: dict[str, Any] = dict(options)
    if isinstance(kwargs.get("method"), HTTPMethod):
        kwargs["method"] = kwargs["method"].value
    send = _pyfetch_0_26 if pyodide_version == "0.26.0a2" else pyfetch
    response = await send(resource, **kwargs)
    return Response(response.js_response)
