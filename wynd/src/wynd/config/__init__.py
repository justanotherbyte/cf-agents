"""The Python form of cloudflare.config.ts, for cloudflare.config.py.

The config module sets `config` to a `Config`, an awaitable of one, or a
factory of the `Context`. Bindings live on an Env class in the source
directory, which Worker code imports under TYPE_CHECKING only, because Wynd
isn't installed in the Worker:

    # src/env.py
    from wynd.config import bindings, context

    class Env:
        DB = bindings.d1(name="db")
        CACHE = bindings.kv() if context().mode == "staging" else None

    # cloudflare.config.py
    from env import Env
    from wynd.config import Config, Worker

    config = Config(worker=Worker(name="app", compatibility_date="2026-09-08",
        compatibility_flags=["python_workers"], entrypoint="src/main.py", env=Env))
"""

from . import bindings, exports, triggers
from .context import Context, Input, context
from .definitions import Config, Container, Worker

__all__ = (
    "Config",
    "Container",
    "Context",
    "Input",
    "Worker",
    "bindings",
    "context",
    "exports",
    "triggers",
)
