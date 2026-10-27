"""The auth context a request's tools can read (upstream ``auth-context.ts``)."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from .types import McpAuthContext

__all__ = ("get_mcp_auth_context",)

# A ContextVar, as upstream's is AsyncLocalStorage: the request's task copies it.
_current: ContextVar[McpAuthContext | None] = ContextVar(
    "agents_mcp_auth_context", default=None
)


def get_mcp_auth_context() -> McpAuthContext | None:
    """Return the ``auth_context`` the handler serving this request was given.

    Returns
    -------
    McpAuthContext | None
        ``None`` outside a request, or when the handler has no
        ``auth_context``.
    """
    return _current.get()


@contextmanager
def auth_context(context: McpAuthContext | None) -> Generator[None]:
    """Set the auth context for tasks started inside the block."""
    token = _current.set(context)
    try:
        yield
    finally:
        _current.reset(token)
