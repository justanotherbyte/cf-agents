from typing import Any as _Any

from .bindings import _EnvWrapper
from .blob import Blob, BlobEnding, BlobValue, File
from .context import js_promise as _js_promise
from .entrypoints import DurableObject, WorkerEntrypoint, WorkflowEntrypoint, handler
from .fetch import fetch
from .formdata import FormData, FormDataValue
from .request import Request
from .response import FetchResponse, Response
from .rpc import python_from_rpc, python_to_rpc
from .types import (
    Body,
    Context,
    FetchKwargs,
    Headers,
    JSBody,
    RequestInitCfProperties,
)
from .utils import import_from_javascript, patch_env

__all__ = [
    "Blob",
    "BlobEnding",
    "BlobValue",
    "Body",
    "Context",
    "DurableObject",
    "FetchKwargs",
    "FetchResponse",
    "File",
    "FormData",
    "FormDataValue",
    "Headers",
    "JSBody",
    "Request",
    "RequestInitCfProperties",
    "Response",
    "WorkerEntrypoint",
    "WorkflowEntrypoint",
    "env",
    "fetch",
    "handler",
    "import_from_javascript",
    "patch_env",
    "python_from_rpc",
    "python_to_rpc",
    "waitUntil",
    "wait_until",
]


def _wait_until(awaitable: _Any) -> None:
    """Hand `awaitable` to cloudflare:workers' waitUntil, as the stock SDK does.

    Under wrangler dev that waitUntil doesn't keep a Python Worker alive past
    the response, with this or with the stock SDK. `ctx.wait_until` does.
    """
    import_from_javascript("cloudflare:workers").waitUntil(_js_promise(awaitable))


def __getattr__(name: str) -> _Any:
    # Served on access, as in the stock SDK: importing workers holds no JS
    # object, which snapshot creation requires.
    if name == "env":
        return _EnvWrapper(import_from_javascript("cloudflare:workers").env)
    if name in ("wait_until", "waitUntil"):
        # workerd's own waitUntil drops a coroutine handed to it, so this
        # passes a promise that follows it, as ctx.wait_until does.
        return _wait_until
    raise AttributeError(f"module 'workers' has no attribute {name!r}")
