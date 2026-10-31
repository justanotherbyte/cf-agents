import asyncio
from collections.abc import Awaitable, Callable

import pytest
from fake_runtime import FakeCtx, Host

from agents.harness import (
    Approval,
    Harness,
    OperationEvent,
    OperationNotFoundError,
    SessionBusyError,
    SessionNotFoundError,
    SessionSnapshot,
    TextEvent,
    ToolRequest,
    Turn,
    TurnResult,
    tool,
)
from agents.lifecycle import Lifecycle, MemoryLimitContext

type Script = Callable[[Turn], Awaitable[TurnResult]]


async def echo(turn: Turn) -> TurnResult:
    """Append the prompt to the history and answer with it."""
    turn.emit(TextEvent(operation_id=turn.operation_id, delta=str(turn.input)))

    history = (turn.history or b"") + str(turn.input).encode() + b";"
    return TurnResult(history=history, text=f"echo {turn.input}")


class ScriptedHarness(Harness):
    def __init__(
        self,
        script: Script = echo,
        *,
        id: str = "test-harness",
        target: object = None,
        max_restarts: int = 5,
    ) -> None:
        super().__init__(id, target=target, max_restarts=max_restarts)

        self.script = script
        self.turns: list[Turn] = []

    async def run_turn(self, turn: Turn) -> TurnResult:
        self.turns.append(turn)
        return await self.script(turn)


def setup(
    script: Script = echo, host: Host | None = None, *, max_restarts: int = 5
) -> tuple[Lifecycle, ScriptedHarness, Host]:
    host = host or Host()
    harness = ScriptedHarness(script, max_restarts=max_restarts)
    return Lifecycle(host).use(harness), harness, host


def restarted(host: Host) -> Host:
    """A new host on the same storage: the object after its isolate died."""
    new = Host()
    new.ctx = FakeCtx(storage=host.ctx.storage)
    return new


class Blocker:
    """A script that saves a checkpoint, then blocks until released."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, turn: Turn) -> TurnResult:
        turn.save(b"checkpoint:" + str(turn.input).encode())
        self.started.set()
        await self.release.wait()
        return await echo(turn)


def test_prompt_runs_a_turn_and_keeps_history() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        first = await harness.prompt("hi")
        second = await harness.prompt("again")

        assert (first.status, first.text) == ("done", "echo hi")
        assert second.text == "echo again"
        assert harness.turns[1].history == b"hi;"
        assert harness.turns[0].attempt == 0

    asyncio.run(scenario())


def test_submit_dedupes_by_operation_id() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        session = harness.session()
        first = await session.submit("hi", operation_id="op-1")
        second = await session.submit("hi", operation_id="op-1")
        await session.wait("op-1")

        assert (first.accepted, second.accepted) == (True, False)
        assert len(harness.turns) == 1

    asyncio.run(scenario())


def test_operations_run_one_at_a_time_in_order() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        await session.submit("a", operation_id="a")
        await session.submit("b", operation_id="b")
        await blocker.started.wait()
        assert [op.status for op in await session.pending()] == ["running", "queued"]

        blocker.release.set()
        await session.wait("b")
        assert [turn.input for turn in harness.turns] == ["a", "b"]

    asyncio.run(scenario())


def test_reject_when_busy() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        await session.submit("a", operation_id="a")
        with pytest.raises(SessionBusyError):
            await session.submit("b", when_busy="reject")

        receipt = await session.submit("a", operation_id="a", when_busy="reject")
        assert not receipt.accepted

        blocker.release.set()
        await session.wait("a")

    asyncio.run(scenario())


def test_wait_for_an_unknown_operation_raises() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        with pytest.raises(OperationNotFoundError):
            await harness.session().wait("nope")

    asyncio.run(scenario())


def test_failing_turn_settles_unanswered_and_retry_resumes() -> None:
    failures = [RuntimeError("model down")]

    async def flaky(turn: Turn) -> TurnResult:
        turn.save(b"step-1")
        if failures:
            raise failures.pop()

        return await echo(turn)

    _, harness, _ = setup(flaky)

    async def scenario() -> None:
        session = harness.session()
        failed = await session.prompt("hi", operation_id="op")
        assert (failed.status, failed.reason) == ("unanswered", "model down")

        await session.retry("op")
        result = await session.wait("op")
        assert result.status == "done"
        assert harness.turns[1].checkpoint == b"step-1"

        with pytest.raises(ValueError, match="unanswered"):
            await session.retry("op")

    asyncio.run(scenario())


def test_needs_input_then_answers_as_a_new_operation() -> None:
    request = ToolRequest(id="call-1", name="delete", args={"id": 3}, kind="approval")

    async def approval(turn: Turn) -> TurnResult:
        if isinstance(turn.input, str):
            return TurnResult(
                history=b"asked", status="needs_input", requests=[request]
            )

        return TurnResult(history=b"done", text=f"answered {turn.input[0].id}")

    _, harness, _ = setup(approval)

    async def scenario() -> None:
        session = harness.session()
        asked = await session.prompt("delete note 3")
        assert (asked.status, asked.requests) == ("needs_input", [request])

        answered = await session.prompt([Approval(id="call-1", approved=True)])
        assert answered.text == "answered call-1"
        assert harness.turns[1].input == [Approval(id="call-1", approved=True)]

    asyncio.run(scenario())


def test_abort_cancels_the_running_turn_and_runs_the_next() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        session = harness.session()
        await session.submit("a", operation_id="a")
        await session.submit("b", operation_id="b")
        await blocker.started.wait()
        await session.abort("a")
        assert (await session.wait("a")).status == "aborted"

        blocker.release.set()
        assert (await session.wait("b")).status == "done"
        assert harness.lifecycle.storage.sql.exec(
            "SELECT history FROM cf_agents_harness_sessions WHERE id = ''"
        ).toArray() == [{"history": b"b;"}]

    asyncio.run(scenario())


def test_wake_job_resumes_a_turn_from_its_checkpoint_after_a_crash() -> None:
    blocker = Blocker()
    _, dead, host = setup(blocker)

    async def scenario() -> None:
        await dead.session().submit("hi", operation_id="op")
        await blocker.started.wait()

        lifecycle, harness, _ = setup(host=restarted(host))
        await lifecycle.start()
        await lifecycle.alarm()
        result = await harness.session().wait("op")

        assert result.text == "echo hi"
        assert harness.turns[0].checkpoint == b"checkpoint:hi"
        assert harness.turns[0].attempt == 1
        await dead.dispose()

    asyncio.run(scenario())


def test_turn_settles_unanswered_after_max_restarts() -> None:
    blocker = Blocker()
    _, dead, host = setup(blocker)

    async def scenario() -> None:
        await dead.session().submit("hi", operation_id="op")
        await blocker.started.wait()
        host.ctx.storage.sql.exec(
            "UPDATE cf_agents_harness_operations SET attempts = 2"
        )

        lifecycle, harness, _ = setup(host=restarted(host), max_restarts=2)
        await lifecycle.start()
        await lifecycle.alarm()
        result = await harness.session().wait("op")

        assert (result.status, result.reason) == ("unanswered", "interrupted")
        assert harness.turns == []
        await dead.dispose()

    asyncio.run(scenario())


def test_platform_failure_leaves_the_turn_running_for_recovery() -> None:
    async def evicted(turn: Turn) -> TurnResult:
        turn.save(b"step-1")
        raise RuntimeError("Durable Object reset because its code was updated.")

    _, harness, _ = setup(evicted)

    async def scenario() -> None:
        await harness.session().submit("hi", operation_id="op")
        await asyncio.sleep(0)

        [pending] = await harness.session().pending()
        assert pending.status == "running"

    asyncio.run(scenario())


def test_two_harnesses_share_one_object() -> None:
    host = Host()
    first = ScriptedHarness(id="first")
    second = ScriptedHarness(id="second")
    Lifecycle(host).use(first).use(second)

    async def scenario() -> None:
        assert (await first.prompt("a")).text == "echo a"
        assert (await second.prompt("b")).text == "echo b"

    asyncio.run(scenario())


def test_sealed_memory_limit_settles_running_turns() -> None:
    blocker = Blocker()
    _, harness, _ = setup(blocker)

    async def scenario() -> None:
        await harness.session().submit("hi", operation_id="op")
        await blocker.started.wait()
        await harness.on_memory_limit(MemoryLimitContext(sealed=True))
        result = await harness.session().wait("op")

        assert (result.status, result.reason) == ("unanswered", "out_of_memory")
        await harness.dispose()

    asyncio.run(scenario())


def test_wake_job_completes_once_no_work_is_open() -> None:
    lifecycle, harness, _ = setup()

    async def scenario() -> None:
        await harness.prompt("hi")
        await lifecycle.alarm()

        assert harness.lifecycle.jobs.list() == []

    asyncio.run(scenario())


def test_fork_copies_history_and_reset_clears_it() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        await harness.prompt("hi")
        fork = await harness.sessions.fork("")
        await fork.prompt("more")
        assert harness.turns[-1].history == b"hi;"

        await harness.session().reset()
        await harness.prompt("fresh")
        assert harness.turns[-1].history is None

        assert await harness.sessions.list() == ["", fork.id]
        with pytest.raises(SessionNotFoundError):
            await harness.sessions.fork("missing")

    asyncio.run(scenario())


def test_watch_yields_a_snapshot_then_events() -> None:
    _, harness, _ = setup()

    async def scenario() -> None:
        events = harness.session().watch()
        assert await anext(events) == SessionSnapshot(operations=[])

        await harness.session().prompt("hi", operation_id="op")
        seen = [await anext(events) for _ in range(4)]
        assert seen == [
            OperationEvent(operation_id="op", status="queued"),
            OperationEvent(operation_id="op", status="running"),
            TextEvent(operation_id="op", delta="hi"),
            OperationEvent(operation_id="op", status="done"),
        ]
        await events.aclose()

    asyncio.run(scenario())


class Notes:
    @tool(replay="safe")
    async def search(self, query: str) -> list[str]:
        return [query]

    @tool(requires_approval=True)
    async def delete(self, note_id: int) -> str:
        return f"deleted {note_id}"

    async def helper(self) -> None: ...


class Overridden(Notes):
    async def delete(self, note_id: int) -> str:
        return ""


def test_tools_are_the_targets_marked_methods_bound() -> None:
    harness = ScriptedHarness(target=Notes())
    specs = {spec.name: spec for spec in harness.tools}

    assert set(specs) == {"search", "delete"}
    assert (specs["search"].replay, specs["search"].requires_approval) == (
        "safe",
        False,
    )
    assert specs["delete"].requires_approval
    assert asyncio.run(specs["search"].fn("x")) == ["x"]

    assert [spec.name for spec in ScriptedHarness(target=Overridden()).tools] == [
        "search"
    ]


def test_tool_rejects_sync_functions_and_bad_replay() -> None:
    with pytest.raises(TypeError, match="async"):

        @tool
        def sync(self) -> None: ...

    with pytest.raises(ValueError, match="replay"):
        tool(replay="sometimes")
