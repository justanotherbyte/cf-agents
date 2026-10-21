"""What Wynd loads cloudflare.config.py for: the mode and whether it builds a
preview."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

__all__ = ("Context", "Input", "context")


@dataclass(frozen=True, slots=True)
class Context:
    """The mode, from `-e` or CLOUDFLARE_ENV, and whether the command is
    `wynd preview`."""

    mode: str | None = None
    is_preview: bool = False


_current: ContextVar[Context] = ContextVar("wynd.config.context")

# A value, an awaitable of it, or a factory of the context that returns either.
type Input[T] = T | Awaitable[T] | Callable[[Context], T | Awaitable[T]]


def context() -> Context:
    """The context Wynd is loading the config for. An Env class body reads it
    to choose its bindings. Outside a Wynd load it is `Context()`."""
    return _current.get(Context())
