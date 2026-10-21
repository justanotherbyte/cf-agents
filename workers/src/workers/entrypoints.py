import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

import _pyodide_entrypoint_helper
from pyodide.code import relaxed_call

from ._js import js_name
from .bindings import Binding, _convert, _EnvWrapper
from .context import DurableObjectAbort, DurableObjectContext, ExecutionContext
from .request import Request
from .workflows import wrap_run

__all__ = (
    "DurableObject",
    "DurableObjectAbort",
    "DurableObjectContext",
    "WorkerEntrypoint",
    "WorkflowEntrypoint",
    "handler",
)

EnvT = TypeVar("EnvT")

if TYPE_CHECKING:

    class _Typed[Env]:
        env: Env

else:

    class _Typed:
        # Generic only to type checkers. A class that is generic at runtime
        # can't follow Generic[S] in a base list (Python reports an
        # inconsistent MRO), so subscripting here just returns the class and
        # Generic mixes in before or after an entrypoint.
        def __class_getitem__(cls, item: Any) -> type:
            return cls


class _Entrypoint:
    _context_class: Callable[[Any], Any] | None = None
    # Handlers workerd calls with raw JS arguments, which get converted.
    _raw_handlers: tuple[str, ...] = ()
    # Handlers workerd calls as on_<name> on dates before 2025-08-14, from
    # makeEntrypointProxyHandler in workerd's python-entrypoint-helper.ts.
    _legacy_names: tuple[str, ...] = ()

    ctx: Any
    env: Any

    def __init__(self, ctx: Any, env: Any) -> None:
        self.ctx, self.env = _wrap_ctx_and_env(type(self), ctx, env)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "__init__" in cls.__dict__:
            init = _init_with_wrapped_args(cls.__dict__["__init__"])
            setattr(cls, "__init__", init)  # noqa: B010 (ty rejects the plain assignment)

        for name in (*cls._raw_handlers, *(f"on_{n}" for n in cls._raw_handlers)):
            handler = getattr(cls, name, None)
            if handler is not None and handler not in _ALREADY_WRAPPED:
                setattr(cls, name, _converting(handler))

        for name in cls._legacy_names:
            if hasattr(cls, name) and not hasattr(cls, f"on_{name}"):
                setattr(cls, f"on_{name}", _alias(name))


def _wrap_ctx_and_env(cls: type[_Entrypoint], ctx: Any, env: Any) -> tuple[Any, Any]:
    if not isinstance(ctx, Binding):
        # Lets ctx.waitUntil take a coroutine, as in the stock SDK. A ctx this
        # wrapped already went through here; workerd ignores a repeat patch.
        _pyodide_entrypoint_helper.patchWaitUntil(ctx)
        if cls._context_class is not None:
            ctx = cls._context_class(ctx)

    return ctx, _EnvWrapper(env)


def _init_with_wrapped_args(init: Callable[..., None]) -> Callable[..., None]:
    # The stock SDK wraps ctx and env before the user's __init__ sees them.
    @functools.wraps(init)
    def wrapped(self: Any, ctx: Any, env: Any, *args: Any, **kwargs: Any) -> None:
        init(self, *_wrap_ctx_and_env(type(self), ctx, env), *args, **kwargs)

    return wrapped


_ALREADY_WRAPPED: set[Callable[..., Any]] = set()


def _handler_arg(value: Any) -> Any:
    # A binary WebSocket message stays an ArrayBuffer, as in the stock SDK.
    return value if js_name(value) == "ArrayBuffer" else _convert(value)


def _converting(handler: Callable[..., Any]) -> Callable[..., Any]:
    # wraps() keeps the handler's signature visible to workerd's relaxed_call.
    @functools.wraps(handler)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        return handler(
            self,
            *map(_handler_arg, args),
            **{k: _handler_arg(v) for k, v in kwargs.items()},
        )

    _ALREADY_WRAPPED.add(wrapped)
    return wrapped


def _alias(name: str) -> Callable[..., Any]:
    def alias(self: Any, *args: Any, **kwargs: Any) -> Any:
        # workerd passes on_fetch (request, env, ctx). Relaxing drops what the
        # handler's signature doesn't take, as workerd does for a plain fetch.
        return relaxed_call(getattr(self, name), *args, **kwargs)

    _ALREADY_WRAPPED.add(alias)
    return alias


class WorkerEntrypoint(_Typed[EnvT], _Entrypoint):
    _context_class = ExecutionContext
    _raw_handlers = ("connect", "queue")
    _legacy_names = ("fetch", "connect")

    ctx: ExecutionContext


class DurableObject(_Typed[EnvT], _Entrypoint):
    _context_class = DurableObjectContext
    _raw_handlers = (
        "connect",
        "alarm",
        "webSocketMessage",
        "webSocketClose",
        "webSocketError",
    )
    _legacy_names = (
        "fetch",
        "connect",
        "alarm",
        "webSocketMessage",
        "webSocketClose",
        "webSocketError",
    )

    ctx: DurableObjectContext


class WorkflowEntrypoint(_Typed[EnvT], _Entrypoint):
    # The stock SDK leaves a Workflow's ctx raw.

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "run" in cls.__dict__:
            setattr(cls, "run", wrap_run(cls.__dict__["run"]))  # noqa: B010


def handler(func: Callable[..., Any]) -> Callable[..., Any]:
    """For a module-level handler such as `on_fetch`, which workerd calls only
    on dates before 2025-08-14: converts a JS Request argument to a Request
    and wraps the env, as in the stock SDK."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if args and js_name(args[0]) == "Request":
            args = (Request(args[0]), *args[1:])
        if len(args) > 1:
            args = (args[0], _EnvWrapper(args[1]), *args[2:])

        return func(*args, **kwargs)

    return wrapper
