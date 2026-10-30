---
title: "Keep a Python agent running during long work"
description: "Keep an agent in memory while slow work runs, checkpoint it with fibers, recover it after the isolate stops, and track managed fibers by id."
---

The runtime can evict an agent from memory when no request, message or alarm
is active, even while a coroutine is still waiting on a slow API. This page
covers two ways to prevent that and to recover when it happens anyway.

| You need                                                | Use                                   |
| ------------------------------------------------------- | ------------------------------------- |
| The agent to stay in memory while one coroutine runs    | `keep_alive_while`                    |
| To resume from a checkpoint if the isolate stops anyway | A fiber, with `run_fiber` or `start_fiber` |
| Steps that each run exactly once, with retries          | A [durable task](tasks.md)         |

## Keep the agent in memory

`keep_alive_while(fn)` runs `fn` and keeps the agent in memory until it
finishes, however it finishes:

```python
from agents import Agent, callable


class Research(Agent):
    @callable
    async def research(self, topic: str) -> str:
        return await self.keep_alive_while(lambda: self._slow_search(topic))

    async def _slow_search(self, topic: str) -> str:
        ...
```

While it runs, the SDK sets the agent's alarm every `keep_alive_interval`, 30
seconds by default. Each alarm keeps the agent awake.

`keep_alive()` does the same without a function. It returns a handle. Call
`handle.dispose()` when the work is done:

```python
handle = await self.keep_alive()
try:
    await self._slow_search(topic)
finally:
    handle.dispose()
```

Keep-alive doesn't protect against a deploy, a crash or the runtime's memory
limit. For those, use a fiber.

## Checkpoint work with a fiber

A fiber is a coroutine the SDK records while it runs. It keeps the agent alive
like `keep_alive_while`. If the isolate stops before the fiber finishes, the
SDK calls `on_fiber_recovered` the next time the agent wakes, with the last
checkpoint you saved:

```python
from agents import (
    Agent,
    FiberContext,
    FiberRecoveryContext,
    FiberRecoveryResult,
    QueueItem,
)


class Importer(Agent):
    async def import_all(self, urls: list[str]) -> int:
        async def body(ctx: FiberContext) -> int:
            for i, url in enumerate(urls):
                await self._import(url)
                ctx.stash({"done": i + 1, "urls": urls})
            return len(urls)

        return await self.run_fiber("import", body)

    async def on_fiber_recovered(
        self, ctx: FiberRecoveryContext
    ) -> FiberRecoveryResult | None:
        if ctx.name == "import" and ctx.snapshot:
            remaining = ctx.snapshot["urls"][ctx.snapshot["done"] :]
            await self.queue(self.resume_import, {"urls": remaining})
        return None

    async def resume_import(self, payload: dict, item: QueueItem) -> None:
        await self.import_all(payload["urls"])
```

`_import` stands for your own code that imports one URL. The recovery hook
queues the URLs that weren't done yet, and the queue runs `import_all` again
with only those.

- `ctx.stash(data)` saves a checkpoint, replacing the previous one. `data`
  must be JSON-serializable. It's saved before `stash` returns.
- `run_fiber` returns what the body returns. Errors propagate and the SDK
  doesn't retry them.
- `on_fiber_recovered` runs before `on_start`. The default logs a warning.

If `on_fiber_recovered` raises for a fiber from `run_fiber`, the SDK calls it
again on later wakes, for up to `options.fiber_recovery_max_age` after the
fiber started. That is 24 hours by default, and `None` retries forever. For a
managed fiber from `start_fiber`, raising marks the record `error` instead.

## Track fibers by id

`start_fiber` starts a managed fiber in the background and keeps a record you
can inspect, list and cancel:

```python
from agents import Agent, FiberContext, callable


class Exporter(Agent):
    @callable
    async def export(self, account_id: str) -> str:
        async def body(ctx: FiberContext) -> None:
            ctx.stash({"step": "started"})
            await self._export(account_id)

        started = await self.start_fiber(
            "export", body, idempotency_key=f"export-{account_id}"
        )
        return started.fiber_id
```

`start_fiber` returns a `StartFiberResult`, a record with `fiber_id`, `name`,
`status`, `snapshot` and `accepted`. The body's return value is discarded, so
save results with `ctx.stash` or in SQL. A second `start_fiber` with the same
`fiber_id` or `idempotency_key` returns the existing fiber with
`accepted=False` instead of starting another. If the two name different
existing fibers, it raises `FiberConflictError`.

Pass `wait_for_completion=True` to return only once the fiber has finished.
If the record is deleted while you wait, the call raises
`FiberNotFoundError`.

| Method                                    | What it does                                               |
| ----------------------------------------- | ---------------------------------------------------------- |
| `inspect_fiber(fiber_id)`                 | Returns the record, or `None`.                             |
| `inspect_fiber_by_key(key)`               | Looks the record up by idempotency key.                    |
| `list_fibers(status=, name=, limit=)`     | Returns records, newest first. 50 by default, at most 100. |
| `cancel_fiber(fiber_id, reason)`          | Aborts a live fiber. Its body gets `CancelledError` at its next `await`. `False` if it's unknown or finished. |
| `resolve_fiber(fiber_id, result)`         | Settles an `interrupted` fiber yourself. `False` for any other status. |
| `delete_fibers(status=, settled_before=, limit=)` | Deletes `completed`, `error` and `aborted` records by default, 100 at a time (at most 500). Returns how many. |

A record's `status` is one of these:

| `status`        | Enters it when                                                   | What to do                                              |
| --------------- | ---------------------------------------------------------------- | ------------------------------------------------------- |
| `"pending"`     | `start_fiber` saved it and the body hasn't started.              | Wait, or `cancel_fiber`.                                |
| `"running"`     | The body is running.                                             | Wait, or `cancel_fiber`.                                |
| `"completed"`   | The body returned, or recovery returned `FiberCompleted`.        | Read `snapshot`. Delete the record when done.           |
| `"error"`       | The body raised, recovery raised, or recovery returned `FiberErrored`. | Read `error`, the exception's message.            |
| `"aborted"`     | `cancel_fiber` stopped it, or recovery returned `FiberAborted`.  | Nothing. Delete the record when done.                   |
| `"interrupted"` | The isolate stopped mid-run, and recovery didn't settle it.      | Settle it with `resolve_fiber`, or delete it by status. |

To settle an interrupted fiber from `on_fiber_recovered`, return
`FiberCompleted(snapshot=...)`, `FiberErrored(error=...)` or
`FiberAborted(reason=...)`. Returning `None` or `FiberInterrupted(reason=...)`
leaves it `interrupted`.
