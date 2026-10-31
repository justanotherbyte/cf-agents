"""Handles on a harness's sessions, matching upstream's ``AgentHarness`` names."""

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING

from .types import (
    HarnessEvent,
    HarnessInput,
    OperationResult,
    PendingOperation,
    Receipt,
    SessionSnapshot,
    WhenBusy,
)

if TYPE_CHECKING:
    from .harness import Harness

__all__ = ("HarnessSession", "HarnessSessions")


class HarnessSession:
    """One conversation: its history and its operations. No I/O until called.

    Parameters
    ----------
    harness
        The owning harness.
    id
        The session id (``""`` is the root session).
    """

    __slots__ = ("_harness", "id")

    def __init__(self, harness: "Harness", id: str) -> None:
        self._harness = harness
        self.id = id

    def __repr__(self) -> str:
        """Return ``<HarnessSession id='...'>``."""
        return f"<HarnessSession id={self.id!r}>"

    async def submit(
        self,
        input: HarnessInput,
        *,
        operation_id: str | None = None,
        when_busy: WhenBusy = "queue",
    ) -> Receipt:
        """Durably queue a prompt or tool answers; returns before the turn runs.

        Parameters
        ----------
        input
            A prompt, or answers to a ``needs_input`` result's requests.
        operation_id
            Dedupes retries: submitting an existing id stores nothing new and
            returns a receipt with ``accepted=False``. Generated when omitted.
        when_busy
            ``"queue"`` behind open work, or ``"reject"`` it.

        Raises
        ------
        SessionBusyError
            If ``when_busy="reject"`` and the session has open work.
        """
        return await self._harness._submit(
            self.id, input, operation_id=operation_id, when_busy=when_busy
        )

    async def wait(self, operation_id: str) -> OperationResult:
        """Wait for an operation to settle and return its result.

        Raises
        ------
        OperationNotFoundError
            If the session has no such operation.
        """
        return await self._harness._wait(self.id, operation_id)

    async def prompt(
        self, input: HarnessInput, *, operation_id: str | None = None
    ) -> OperationResult:
        """Submit and wait for the result."""
        receipt = await self.submit(input, operation_id=operation_id)

        return await self.wait(receipt.operation_id)

    async def pending(self) -> Sequence[PendingOperation]:
        """Return the open operations, oldest first."""
        return await self._harness._pending(self.id)

    async def abort(self, operation_id: str | None = None) -> None:
        """Stop one open operation, or all of them; queued work behind runs."""
        await self._harness._abort(self.id, operation_id)

    async def retry(self, operation_id: str) -> None:
        """Run an ``unanswered`` operation again, from its last checkpoint.

        Raises
        ------
        OperationNotFoundError
            If the session has no such operation.
        ValueError
            If the operation isn't ``unanswered``.
        """
        await self._harness._retry(self.id, operation_id)

    async def reset(self) -> None:
        """Clear the session's history.

        Raises
        ------
        SessionBusyError
            If the session has open work.
        """
        await self._harness._reset(self.id)

    async def watch(self) -> AsyncIterator[HarnessEvent]:
        """Yield a `SessionSnapshot` of open operations, then every event."""
        queue = self._harness._subscribe(self.id)
        try:
            yield SessionSnapshot(operations=await self.pending())
            while True:
                yield await queue.get()
        finally:
            self._harness._unsubscribe(self.id, queue)


class HarnessSessions:
    """Creating, forking, and listing a harness's sessions."""

    __slots__ = ("_harness",)

    def __init__(self, harness: "Harness") -> None:
        self._harness = harness

    async def create(self) -> HarnessSession:
        """Create an empty session."""
        return await self._harness._create_session()

    async def fork(self, id: str) -> HarnessSession:
        """Create a session with a copy of session ``id``'s history.

        Raises
        ------
        SessionNotFoundError
            If session ``id`` doesn't exist.
        """
        return await self._harness._fork_session(id)

    async def list(self) -> Sequence[str]:
        """Return every session id, oldest first."""
        return await self._harness._session_ids()
