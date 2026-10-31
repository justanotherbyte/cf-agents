"""Types for harnesses: inputs, turns, operation results, and events."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ..core.types import JSONValue

__all__ = (
    "Approval",
    "HarnessEvent",
    "HarnessInput",
    "OperationEvent",
    "OperationResult",
    "OperationStatus",
    "PendingOperation",
    "Receipt",
    "Replay",
    "SessionSnapshot",
    "TextEvent",
    "ToolAnswer",
    "ToolCallEvent",
    "ToolOutput",
    "ToolRequest",
    "ToolResultEvent",
    "ToolSpec",
    "Turn",
    "TurnResult",
    "WhenBusy",
)

type OperationStatus = Literal[
    "queued", "running", "done", "needs_input", "unanswered", "aborted"
]
"""``queued`` and ``running`` are open; the rest are settled. ``needs_input``
means the turn stopped on tool calls the client must answer with a new
operation."""

type WhenBusy = Literal["queue", "reject"]
"""What ``submit`` does when the session has open work: queue behind it, or
raise `SessionBusyError`."""

type Replay = Literal["safe", "unsafe"]
"""Whether a tool may run again after a crash interrupted it."""


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolSpec:
    """A ``@tool`` method bound to its host, as a harness receives it."""

    fn: Callable[..., Awaitable[Any]]
    name: str
    requires_approval: bool = False
    replay: Replay = "unsafe"


@dataclass(slots=True, frozen=True, kw_only=True)
class Approval:
    """The client's decision on a tool call that needs approval."""

    id: str
    approved: bool
    message: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolOutput:
    """The result of a tool call the client ran itself."""

    id: str
    value: JSONValue


type ToolAnswer = Approval | ToolOutput
"""An answer to one of a ``needs_input`` result's requests."""

type HarnessInput = str | Sequence[ToolAnswer]
"""What an operation carries: a user prompt, or answers to tool requests."""


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolRequest:
    """A tool call the turn stopped on: approve it, or run it and send the output."""

    id: str
    name: str
    args: dict[str, JSONValue]
    kind: Literal["approval", "call"]


@dataclass(slots=True, frozen=True, kw_only=True)
class Receipt:
    """What ``submit`` returns once the operation is stored.

    ``accepted`` is ``False`` when an operation with this id already existed.
    """

    session: str
    operation_id: str
    accepted: bool


@dataclass(slots=True, frozen=True, kw_only=True)
class OperationResult:
    """A settled operation."""

    session: str
    operation_id: str
    status: OperationStatus
    text: str | None = None
    requests: Sequence[ToolRequest] = ()
    reason: str | None = None


@dataclass(slots=True, frozen=True, kw_only=True)
class PendingOperation:
    """An open operation."""

    session: str
    operation_id: str
    status: Literal["queued", "running"]


@dataclass(slots=True, frozen=True, kw_only=True)
class SessionSnapshot:
    """The first event ``watch`` yields: the session's open operations."""

    operations: Sequence[PendingOperation]
    type: Literal["snapshot"] = "snapshot"


@dataclass(slots=True, frozen=True, kw_only=True)
class OperationEvent:
    """An operation changed status."""

    operation_id: str
    status: OperationStatus
    type: Literal["operation"] = "operation"


@dataclass(slots=True, frozen=True, kw_only=True)
class TextEvent:
    """Model text streamed during a turn."""

    operation_id: str
    delta: str
    type: Literal["text"] = "text"


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolCallEvent:
    """A tool started running."""

    operation_id: str
    tool_call_id: str
    name: str
    args: dict[str, JSONValue]
    type: Literal["tool-call"] = "tool-call"


@dataclass(slots=True, frozen=True, kw_only=True)
class ToolResultEvent:
    """A tool finished."""

    operation_id: str
    tool_call_id: str
    result: JSONValue
    is_error: bool = False
    type: Literal["tool-result"] = "tool-result"


type HarnessEvent = (
    SessionSnapshot | OperationEvent | TextEvent | ToolCallEvent | ToolResultEvent
)
"""What ``watch`` yields."""


@dataclass(slots=True, frozen=True, kw_only=True)
class Turn:
    """One turn for ``Harness.run_turn`` to run.

    Parameters
    ----------
    session
        The session id.
    operation_id
        The operation this turn answers.
    input
        The operation's prompt or tool answers.
    history
        The session's history before this turn, as the subclass last returned
        it; ``None`` for a new session.
    checkpoint
        The last ``save``d bytes when resuming after a crash; ``None`` on a
        first run.
    attempt
        0 on a first run, then how many times the turn was interrupted.
    tools
        The host's ``@tool`` methods, bound.
    emit
        Publishes an event to the session's watchers.
    save
        Durably replaces the turn's checkpoint.
    """

    session: str
    operation_id: str
    input: HarnessInput
    history: bytes | None
    checkpoint: bytes | None
    attempt: int
    tools: Sequence[ToolSpec]
    emit: Callable[[HarnessEvent], None]
    save: Callable[[bytes], None]


@dataclass(slots=True, frozen=True, kw_only=True)
class TurnResult:
    """What ``Harness.run_turn`` returns; ``history`` replaces the session's."""

    history: bytes
    status: Literal["done", "needs_input"] = "done"
    text: str | None = None
    requests: Sequence[ToolRequest] = ()
