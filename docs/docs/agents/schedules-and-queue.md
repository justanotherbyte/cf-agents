---
title: "Schedule and queue work in a Python agent"
description: "Run an agent method later, on a cron expression or at a fixed interval, queue work to run in order after a request returns, and set retries."
---

An agent can run one of its own methods later. Schedules run at a time, after a
delay, on a cron expression or at an interval. The queue runs methods as soon
as possible, one at a time, in the order you added them.

The SDK stores both in the agent's database and runs them from its
[alarm](https://developers.cloudflare.com/durable-objects/api/alarms/), so
they survive restarts and run even when no client is connected. Don't
override `alarm()` on an agent.

## Schedule a method

```python
from datetime import timedelta
from typing import Any

from agents import Agent, Schedule


class Reminders(Agent):
    async def remind_later(self, text: str) -> None:
        await self.schedule(timedelta(minutes=10), self.remind, {"text": text})

    async def remind(self, payload: Any, schedule: Schedule) -> None:
        self.broadcast(f"Reminder: {payload['text']}")
```

The callback receives the payload and the `Schedule` record. Pass it as a
method of the agent or as the method's name, `"remind"`. A name the agent
doesn't have raises `ValueError`. The payload must be JSON-serializable.

`when` decides how the schedule runs:

| `when`                    | Runs                            | `schedule.type` |
| ------------------------- | ------------------------------- | --------------- |
| a `datetime`              | once, at that time              | `"scheduled"`   |
| a `timedelta` or a number | once, after that many seconds   | `"delayed"`     |
| a `str`                   | on that cron expression, in UTC | `"cron"`        |

```python
from datetime import UTC, datetime

await self.schedule(datetime(2026, 12, 25, 9, 0, tzinfo=UTC), self.remind, {"text": "presents"})
await self.schedule(30, self.remind, {"text": "in 30 seconds"})
await self.schedule("0 9 * * 1", self.remind, {"text": "weekly sync"})  # Mondays 09:00 UTC
```

A cron expression has five fields, or six with seconds first. The nicknames
`@yearly`, `@annually`, `@monthly`, `@weekly`, `@daily`, `@hourly` and
`@minutely` also work. An invalid expression, or one with no match within
five years, raises `InvalidCronExpressionError`.

## Repeat at an interval

`schedule_every` runs the callback every `interval`, up to 30 days apart:

```python
await self.schedule_every(timedelta(hours=1), self.sync_feeds)
```

If a run is still in progress when the next one is due, the SDK skips the next
one. A run counts as hung after `options.hung_schedule_timeout`, 30 seconds by
default, and then the schedule runs again.

An interval longer than 30 days raises
`ValueError: A schedule interval can't exceed 30 days`. Use a cron expression
for those.

## Avoid duplicate schedules

`on_start` runs on every wake, so creating a schedule there can add a new one
each time. An idempotent schedule returns an existing pending schedule instead
of adding one, when both have the same type, callback and payload, and the
same cron expression or interval. The exact time of a one-off schedule isn't
compared.

Cron and interval schedules are idempotent by default. One-off schedules
aren't, and the SDK logs a warning when `on_start` creates one without
`idempotent`:

```text
Scheduling 'warm_cache' during startup (e.g. in on_start) without idempotent=True creates a new schedule on every wake, ...
```

Pass `idempotent=True` to reuse a match, or `idempotent=False` to keep adding
schedules and silence the warning:

```python
class Feeds(Agent):
    async def on_start(self) -> None:
        await self.schedule_every(timedelta(hours=1), self.sync_feeds)  # created once
        await self.schedule(60, self.warm_cache, idempotent=True)
```

## List and cancel schedules

```python
schedules = await self.list_schedules(type="cron")
one = await self.get_schedule_by_id(schedules[0].id)
cancelled = await self.cancel_schedule(one.id)  # True if it existed
```

`list_schedules` also filters by `id`, and by next run time with `start` and
`end`. Each `Schedule` has `id`, `callback`, `payload`, `type`, `time` (the next
run, as a UTC `datetime`), and `delay`, `cron` or `interval` for its type.

## Queue work

`queue` runs a callback soon after the current request or message, without
making the client wait. Items run one at a time in the order they were added:

```python
from agents import Agent, QueueItem, callable


class Documents(Agent):
    @callable
    async def upload(self, doc_id: str) -> str:
        await self.queue(self.index_document, {"doc_id": doc_id})
        return "queued"

    async def index_document(self, payload: dict, item: QueueItem) -> None:
        ...
```

The callback receives the payload and the `QueueItem`. `queue` returns the
item's id.

Pass `id=` to give an item a stable id. Queuing the same id again replaces the
pending item, so only the last payload runs. Use it to debounce:

```python
await self.queue(self.rebuild_index, None, id="rebuild-index")
```

A blank `id` or an unknown callback raises `ValueError`.

To inspect or cancel items:

| Method                              | Returns                           |
| ----------------------------------- | --------------------------------- |
| `queue_items(callback=None)`        | Pending items in order.           |
| `get_queue(id)`                     | One item, or `None`.              |
| `dequeue(id)`                       | `True` if the item existed.       |
| `dequeue_all()`                     | How many items it cancelled.      |
| `dequeue_all_by_callback(callback)` | How many items for that callback. |

## Retries and failures

The SDK retries a callback that raises. By default the SDK makes 3 attempts.
Before each retry it waits a random time up to
`min(base_delay * 2 ** attempt, max_delay)`, where `base_delay` is
0.1 seconds, `max_delay` is 3 seconds and `attempt` is the attempt that just
failed. Change the default for every schedule and queue item on the class:

```python
from agents import Agent, AgentOptions, RetryOptions


class Feeds(Agent):
    options = AgentOptions(retry=RetryOptions(max_attempts=5, max_delay=30))
```

Or for one call:

```python
await self.queue(self.send_email, payload, retry=RetryOptions(max_attempts=10))
```

After the last attempt fails, the SDK logs an error with the traceback and
calls `on_error(None, error)`:

```text
Queue callback 'send_email' failed after 10 attempts
```

A schedule logs `Schedule callback '<name>' failed after <n> attempts`
instead. The queue then moves on to the next item. A cron or interval
schedule still runs at its next time, and the SDK removes a one-off schedule.
`on_error` can't keep the item. To see this locally, queue a callback that
raises, and watch the `pywrangler dev` output.

For multi-step work where each step should run only once, even across
retries, use a [durable task](tasks.md).
