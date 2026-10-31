"""The base class for harnesses: an agent loop hosted in a Durable Object.

Python's form of upstream's draft ``AgentHarness`` interface
(``agents/harness/*``). Upstream harnesses each write their own queue,
dedupe, wake logic, and events; here the base class owns them and a subclass
implements one turn. Durability follows upstream's ThinkHarness: the
operations table is the record of open work, and one Lifecycle job per
session (``wake:<session>``) brings the object back to finish it.
"""

import asyncio
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from ..core.platform_errors import is_platform_failure
from ..core.timing import to_seconds
from ..core.types import Duration
from ..lifecycle.capability import LifecycleCapability
from ..lifecycle.types import JobContext, JobOutcome, MemoryLimitContext, Reschedule
from .errors import (
    OperationNotFoundError,
    SessionBusyError,
    SessionNotFoundError,
)
from .events import EventHub
from .session import HarnessSession, HarnessSessions
from .store import HarnessStore, OperationRow
from .tools import collect_tools
from .types import (
    HarnessEvent,
    HarnessInput,
    OperationEvent,
    OperationResult,
    OperationStatus,
    PendingOperation,
    Receipt,
    ToolRequest,
    ToolSpec,
    Turn,
    TurnResult,
    WhenBusy,
)

__all__ = ("Harness",)

_log = logging.getLogger("agents.harness")

_WAKE = "wake"
_ROOT = ""


class Harness(LifecycleCapability):
    """Runs an agent loop in a Durable Object, one turn at a time per session.

    Install it like any capability: ``Lifecycle(self).use(harness)`` on a
    plain Durable Object (forward ``alarm`` to the Lifecycle), or
    ``self.use(harness)`` on an `Agent`. The base class stores sessions and
    operations, queues and dedupes submissions, publishes events, and resumes
    turns a crash interrupted. Subclasses implement `run_turn`.

    Parameters
    ----------
    id
        The capability id; also keys this harness's rows, so two harnesses
        on one object need different ids.
    target
        The object whose ``@tool`` methods are the tools (usually the host).
    max_restarts
        How many times a crash-interrupted turn is resumed before its
        operation settles ``unanswered`` with reason ``"interrupted"``.
    heartbeat
        How often a session with open work is checked; the longest a turn
        waits to resume after a crash when no request wakes the object.

    Raises
    ------
    ValueError
        If ``max_restarts`` is negative or ``heartbeat`` isn't positive.
    """

    def __init__(
        self,
        id: str,
        *,
        target: object | None = None,
        max_restarts: int = 5,
        heartbeat: Duration = 30,
    ) -> None:
        if max_restarts < 0:
            raise ValueError("max_restarts must be 0 or more")
        if to_seconds(heartbeat) <= 0:
            raise ValueError("heartbeat must be positive")

        super().__init__(id)
        self.target = target
        """The object the tools were collected from."""
        self.sessions = HarnessSessions(self)
        """Creating, forking, and listing sessions."""

        self._tools = collect_tools(target) if target is not None else []
        self._max_restarts = max_restarts
        self._heartbeat = timedelta(seconds=to_seconds(heartbeat))
        self._wake_prefix = f"{_WAKE}:{id}:"
        self._store_instance: HarnessStore | None = None
        self._events = EventHub()
        self._drives: dict[str, asyncio.Task[None]] = {}
        self._waiters: dict[tuple[str, str], list[asyncio.Future[None]]] = {}

    @property
    def tools(self) -> Sequence[ToolSpec]:
        """The target's ``@tool`` methods, bound."""
        return self._tools

    @property
    def _store(self) -> HarnessStore:
        if self._store_instance is None:
            self._store_instance = HarnessStore(self.lifecycle.sql, self.capability_id)

        return self._store_instance

    async def run_turn(self, turn: Turn) -> TurnResult:
        """Run one turn and return the session's new history.

        Call ``turn.save`` after each step so a resumed turn (``checkpoint``
        set) can skip finished work, and ``turn.emit`` to stream events.
        Raising settles the operation ``unanswered`` with the error as reason.
        """
        raise NotImplementedError

    def session(self, id: str = _ROOT) -> HarnessSession:
        """Return a handle on one session (default: the root session)."""
        return HarnessSession(self, id)

    async def prompt(self, input: HarnessInput) -> OperationResult:
        """Submit to the root session and wait for the result."""
        return await self.session().prompt(input)

    async def on_start(self) -> None:
        """Create the tables and wake every session with open work."""
        self._store.ensure_tables()
        self._store.ensure_session(_ROOT)

        for session in self._store.sessions_with_open_work():
            await self._wake(session)

    async def on_job(self, context: JobContext) -> JobOutcome:
        """Run a session's wake job: resume its open work, or complete."""
        if context.job.fn != _WAKE:
            return None

        session = context.job.id.removeprefix(self._wake_prefix)
        heartbeat = Reschedule(at=datetime.now(UTC) + self._heartbeat)
        if self._is_driving(session):
            return heartbeat
        if not self._store.open(session):
            return None

        self.lifecycle.track_alarm_work(self._ensure_drive(session))
        return heartbeat

    async def on_memory_limit(self, context: MemoryLimitContext) -> None:
        """Settle running turns once the breaker seals; they'd only run out again."""
        if not context.sealed:
            return

        for row in self._store.running():
            self._settle(row.session, row.id, "unanswered", reason="out_of_memory")

    async def dispose(self) -> None:
        """Stop the drives; their turns resume on the next wake."""
        for drive in self._drives.values():
            drive.cancel()

    async def _submit(
        self,
        session: str,
        input: HarnessInput,
        *,
        operation_id: str | None,
        when_busy: WhenBusy,
    ) -> Receipt:
        await self.lifecycle.ready()
        operation_id = operation_id or str(uuid.uuid4())

        duplicate = self._store.get(session, operation_id) is not None
        if not duplicate and when_busy == "reject" and self._store.open(session):
            raise SessionBusyError(session)

        accepted = self.lifecycle.storage.transactionSync(
            partial(self._insert, session, operation_id, input)
        )
        if accepted:
            self._events.publish(
                session, OperationEvent(operation_id=operation_id, status="queued")
            )

        await self._wake(session)
        self._ensure_drive(session)
        return Receipt(session=session, operation_id=operation_id, accepted=accepted)

    def _insert(self, session: str, operation_id: str, input: HarnessInput) -> bool:
        self._store.ensure_session(session)
        return self._store.insert(session, operation_id, input)

    async def _wait(self, session: str, operation_id: str) -> OperationResult:
        await self.lifecycle.ready()

        while True:
            row = self._store.get(session, operation_id)
            if row is None:
                raise OperationNotFoundError(session, operation_id)
            if not row.is_open:
                return row.result()

            waiter = asyncio.get_running_loop().create_future()
            self._waiters.setdefault((session, operation_id), []).append(waiter)
            await waiter

    async def _pending(self, session: str) -> list[PendingOperation]:
        await self.lifecycle.ready()

        return [row.pending() for row in self._store.open(session)]

    async def _abort(self, session: str, operation_id: str | None) -> None:
        await self.lifecycle.ready()

        rows = [
            row
            for row in self._store.open(session)
            if operation_id is None or row.id == operation_id
        ]
        for row in rows:
            self._settle(session, row.id, "aborted")

        drive = self._drives.get(session)
        if drive is not None and any(row.status == "running" for row in rows):
            drive.cancel()
            await asyncio.wait([drive])

        if self._store.open(session):
            self._ensure_drive(session)

    async def _retry(self, session: str, operation_id: str) -> None:
        await self.lifecycle.ready()

        row = self._store.get(session, operation_id)
        if row is None:
            raise OperationNotFoundError(session, operation_id)
        if row.status != "unanswered":
            raise ValueError(
                f"Only unanswered operations can be retried; {operation_id!r} "
                f"is {row.status}"
            )

        self._store.reopen(session, operation_id)
        self._events.publish(
            session, OperationEvent(operation_id=operation_id, status="queued")
        )

        await self._wake(session)
        self._ensure_drive(session)

    async def _reset(self, session: str) -> None:
        await self.lifecycle.ready()

        if self._store.open(session):
            raise SessionBusyError(session)

        self._store.set_history(session, None)

    async def _create_session(self) -> HarnessSession:
        await self.lifecycle.ready()

        id = str(uuid.uuid4())
        self._store.ensure_session(id)
        return HarnessSession(self, id)

    async def _fork_session(self, source: str) -> HarnessSession:
        await self.lifecycle.ready()

        if not self._store.has_session(source):
            raise SessionNotFoundError(source)

        id = str(uuid.uuid4())
        self._store.fork_session(source, id)
        return HarnessSession(self, id)

    async def _session_ids(self) -> list[str]:
        await self.lifecycle.ready()

        return self._store.session_ids()

    def _subscribe(self, session: str) -> "asyncio.Queue[HarnessEvent]":
        return self._events.subscribe(session)

    def _unsubscribe(self, session: str, queue: "asyncio.Queue[HarnessEvent]") -> None:
        self._events.unsubscribe(session, queue)

    async def _wake(self, session: str) -> None:
        await self.lifecycle.jobs.push(
            id=self._wake_prefix + session,
            fn=_WAKE,
            time=datetime.now(UTC),
            singleflight=True,
            recovery_loop=True,
        )

    def _is_driving(self, session: str) -> bool:
        drive = self._drives.get(session)
        return drive is not None and not drive.done()

    def _ensure_drive(self, session: str) -> asyncio.Task[None]:
        drive = self._drives.get(session)
        if drive is not None and not drive.done():
            return drive

        drive = asyncio.create_task(self._drive(session))
        self._drives[session] = drive
        drive.add_done_callback(partial(self._drive_done, session))
        return drive

    def _drive_done(self, session: str, drive: asyncio.Task[None]) -> None:
        if self._drives.get(session) is drive:
            del self._drives[session]

        if not drive.cancelled() and (error := drive.exception()) is not None:
            _log.error("Harness drive for %r failed", session, exc_info=error)

    async def _drive(self, session: str) -> None:
        while rows := self._store.open(session):
            turn = self._start_turn(rows[0])
            if turn is not None:
                await self._run(turn)

    def _start_turn(self, row: OperationRow) -> Turn | None:
        interrupted = row.status == "running"
        if interrupted and row.attempts >= self._max_restarts:
            self._settle(row.session, row.id, "unanswered", reason="interrupted")
            return None

        attempt = row.attempts + 1 if interrupted else row.attempts
        self._store.start(row.session, row.id, attempts=attempt)
        if not interrupted:
            self._events.publish(
                row.session, OperationEvent(operation_id=row.id, status="running")
            )

        return Turn(
            session=row.session,
            operation_id=row.id,
            input=row.input,
            history=self._store.history(row.session),
            checkpoint=row.checkpoint,
            attempt=attempt,
            tools=self._tools,
            emit=partial(self._events.publish, row.session),
            save=partial(self._store.save_checkpoint, row.session, row.id),
        )

    async def _run(self, turn: Turn) -> None:
        try:
            result = await self.lifecycle.run_in_host_context(
                partial(self.run_turn, turn)
            )
        except Exception as error:
            if is_platform_failure(error):
                raise

            _log.exception("Harness turn %r failed", turn.operation_id)
            self._settle(
                turn.session,
                turn.operation_id,
                "unanswered",
                reason=str(error) or type(error).__name__,
            )
            return

        self._settle(
            turn.session,
            turn.operation_id,
            result.status,
            text=result.text,
            requests=result.requests,
            history=result.history,
        )

    def _settle(
        self,
        session: str,
        operation_id: str,
        status: OperationStatus,
        *,
        text: str | None = None,
        requests: Sequence[ToolRequest] = (),
        reason: str | None = None,
        history: bytes | None = None,
    ) -> None:
        settled = self.lifecycle.storage.transactionSync(
            partial(
                self._write_settlement,
                session,
                operation_id,
                status,
                text=text,
                requests=requests,
                reason=reason,
                history=history,
            )
        )
        if not settled:
            return

        self._events.publish(
            session, OperationEvent(operation_id=operation_id, status=status)
        )
        for waiter in self._waiters.pop((session, operation_id), ()):
            if not waiter.done():
                waiter.set_result(None)

    def _write_settlement(
        self,
        session: str,
        operation_id: str,
        status: OperationStatus,
        *,
        history: bytes | None,
        **result: Any,
    ) -> bool:
        if not self._store.settle(session, operation_id, status, **result):
            return False

        if history is not None:
            self._store.set_history(session, history)
        return True
