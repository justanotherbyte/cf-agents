from collections.abc import Callable
from typing import Any

from pyodide.code import relaxed_call


class _Entrypoint:
    # Handlers workerd calls as on_<name> on dates before 2025-08-14, from
    # makeEntrypointProxyHandler in workerd's python-entrypoint-helper.ts.
    _legacy_names: tuple[str, ...] = ()

    def __init__(self, ctx: Any, env: Any) -> None:
        self.ctx = ctx
        self.env = env

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # The alias looks the handler up on each call, so it follows subclass
        # overrides and is set once. An on_<name> the user defines wins.
        for name in cls._legacy_names:
            if hasattr(cls, name) and not hasattr(cls, f"on_{name}"):
                setattr(cls, f"on_{name}", _alias(name))


def _alias(name: str) -> Callable[..., Any]:
    def alias(self: Any, *args: Any, **kwargs: Any) -> Any:
        # workerd passes on_fetch (request, env, ctx). Relaxing drops what the
        # handler's signature doesn't take, as workerd does for a plain fetch.
        return relaxed_call(getattr(self, name), *args, **kwargs)

    return alias


class WorkerEntrypoint(_Entrypoint):
    _legacy_names = ("fetch", "connect")


class DurableObject(_Entrypoint):
    _legacy_names = (
        "fetch",
        "connect",
        "alarm",
        "webSocketMessage",
        "webSocketClose",
        "webSocketError",
    )


class WorkflowEntrypoint(_Entrypoint):
    pass
