"""``@tool``: tools as ``async`` methods on the host.

The decorator only marks the function, so the method stays callable as
usual; ``Harness(target=obj)`` collects the marked methods bound to ``obj``.
"""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, overload

from .types import Replay, ToolSpec

__all__ = ("collect_tools", "tool")

_MARKER = "__agents_tool__"


@dataclass(slots=True, frozen=True)
class _ToolOptions:
    requires_approval: bool
    replay: Replay


@overload
def tool[F: Callable[..., Awaitable[Any]]](fn: F, /) -> F: ...
@overload
def tool[F: Callable[..., Awaitable[Any]]](
    *, requires_approval: bool = False, replay: Replay = "unsafe"
) -> Callable[[F], F]: ...
def tool[F: Callable[..., Awaitable[Any]]](
    fn: F | None = None,
    /,
    *,
    requires_approval: bool = False,
    replay: Replay = "unsafe",
) -> F | Callable[[F], F]:
    """Make an ``async`` method a tool the harness gives the model.

    The method's name, signature, and docstring describe the tool.

    Parameters
    ----------
    requires_approval
        Stop the turn with a ``needs_input`` result before each call, until
        the client sends an `Approval`.
    replay
        ``"safe"`` lets a crash-interrupted call run again on resume;
        ``"unsafe"`` (the default) reports it to the model as not run.

    Raises
    ------
    TypeError
        If the function isn't ``async``.
    ValueError
        If ``replay`` isn't ``"safe"`` or ``"unsafe"``.

    Examples
    --------
    >>> class Notes(Agent):
    ...     @tool(replay="safe")
    ...     async def search(self, query: str) -> list[str]:
    ...         '''Search the notes.'''
    """
    if replay not in ("safe", "unsafe"):
        raise ValueError(f"replay must be 'safe' or 'unsafe', not {replay!r}")

    options = _ToolOptions(requires_approval, replay)

    def mark(fn: F) -> F:
        _require_async(fn)
        setattr(fn, _MARKER, options)
        return fn

    return mark if fn is None else mark(fn)


def _require_async(fn: object) -> None:
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(f"Tools must be async functions: {fn!r}")


def collect_tools(target: object) -> list[ToolSpec]:
    """Return ``target``'s ``@tool`` methods, bound (the nearest class wins)."""
    nearest: dict[str, object] = {}
    for cls in reversed(type(target).__mro__):
        nearest.update(vars(cls))

    return [
        ToolSpec(
            fn=getattr(target, name),
            name=name,
            requires_approval=options.requires_approval,
            replay=options.replay,
        )
        for name, value in nearest.items()
        if isinstance(options := getattr(value, _MARKER, None), _ToolOptions)
    ]
