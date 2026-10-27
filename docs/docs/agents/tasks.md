---
title: "Run durable multi-step tasks in a Python agent"
description: "Define @task methods whose steps run once and survive restarts, with retries, timeouts, durable sleeps, idempotency keys and run inspection."
---

A task is a method that runs in the background as a series of named steps. The
SDK records each finished step's result in the agent's database. If the
agent restarts, or a step fails and is retried, the task runs again from its
first line, and finished steps return their recorded result instead of running
again.

Use a task when work has side effects that must not repeat, such as charging
a card and then sending a receipt. For a single call, a
[queued callback](schedules-and-queue.md#queue-work) is enough.

## Define a task

Decorate an `async` method with `@task`. It receives the run's input and a
`TaskStep`:

```python
from agents import Agent, TaskStep, TaskStepAttempt, task


class Reports(Agent):
    @task
    async def build(self, input: dict, step: TaskStep) -> dict:
        async def fetch_rows(attempt: TaskStepAttempt) -> int:
            return input["rows"] * 2

        rows = await step.do("fetch rows", fetch_rows)
        await step.sleep("cool down", 1)
        return {"rows": rows}
```

The task's name is the method's name. Its return value is the run's result and
must be JSON-serializable.

## Start a run

`self.build` is a `TaskHandle`. Call `run()` with the input:

```python
from workers import Request, Response


class Reports(Agent):
    async def on_request(self, request: Request) -> Response:
        receipt = await self.build.run({"rows": 21})
        return Response.from_json({"run_id": receipt.run_id, "state": receipt.state})
```

```json
{"run_id": "task_lrIJhjh3QaPOMFAS5UDK1g", "state": "pending"}
```

`run()` saves the run and returns a `TaskReceipt` right away. The run starts in
the background. `run()` also takes `run_id` to choose the id, `metadata` for
JSON kept with the run, and `retain=False` to delete the run's record once it
settles.

To start a run at most once, pass an `idempotency_key`. A second `run()` with
the same key returns the existing run with `receipt.accepted == False`:

```python
receipt = await self.build.run({"rows": 21}, idempotency_key=f"report-{date}")
```

## Write steps

`step.do(name, fn)` runs `fn` once and records its result under `name`. On a
later attempt of the run, it returns the recorded result without calling
`fn`.

Every side effect belongs inside a step. Code outside steps runs again on
every attempt.

```python
@task
async def checkout(self, order: dict, step: TaskStep) -> str:
    async def charge(attempt: TaskStepAttempt) -> str:
        return await payments.charge(
            order["card"], order["total"], idempotency_key=attempt.idempotency_key
        )

    async def send_receipt(attempt: TaskStepAttempt) -> None:
        await mail.send(order["email"], "Thanks for your order")

    charge_id = await step.do("charge", charge)
    await step.do("send receipt", send_receipt)
    return charge_id
```

`attempt.attempt` is the attempt number, starting at 1.
`attempt.idempotency_key` is the same on every attempt of a step. Pass it to
services that deduplicate requests, in case a step ran but the agent stopped
before recording the result.

### Step rules

- Step names must be unique within one run. Reusing a name raises
  `DuplicateTaskStepError`.
- A step name is a non-empty string of up to 256 characters that doesn't
  start with `__cf`. Anything else raises `ValueError`.
- A run can have at most 10,000 steps. The next one raises `RuntimeError`,
  so split larger work across several runs.
- Keep step order and names stable. If you change the steps of a task that
  has runs in progress, those runs can fail with `TaskReplayDivergedError`.
  Rename the task instead, for example `checkout_v2`.
- Step results, the run's input and its result must be JSON-serializable
  and at most 1 MiB (1,048,576 bytes) as JSON. Otherwise the SDK raises
  `TaskSerializationError`.

## Retries and timeouts

A step that raises is retried. By default a step gets 5 attempts. The first
retry waits 1 second and each later one doubles, up to 1 day. Each attempt can
run for 5 minutes. While it waits, the run is `waiting` with
`reason="retry"`, and the agent can leave memory. Set these per step:

```python
from datetime import timedelta

from agents import StepRetries

result = await step.do(
    "call model",
    call_model,
    retries=StepRetries(limit=3, delay=0.5, backoff="linear"),
    timeout=timedelta(seconds=30),
)
```

`limit` counts every attempt, including the first. `delay` is the wait before
the first retry. `backoff` is `"constant"`, `"linear"` or `"exponential"`, and
sets the wait before retry `n` to `delay`, `delay * n` or `delay * 2 ** (n - 1)`.
An attempt that runs past `timeout` raises `StepTimeoutError` and is retried
like any other error.

To fail the run without more retries, raise `NonRetryableError`:

```python
from agents import NonRetryableError


async def charge(attempt: TaskStepAttempt) -> str:
    if order["total"] <= 0:
        raise NonRetryableError("nothing to charge")
    ...
```

When a step runs out of attempts, the run ends as `failed`, the SDK logs
`Task run '<id>' (definition '<name>') failed` with the traceback, and calls
`on_error(None, error)`. An exception raised outside any step fails the run
the same way, at once and without retries. To see it, start a run of a task
whose step raises `NonRetryableError`, then `get()` it:

```python
run = await self.checkout.get(run_id)
print(run.state, run.error)  # failed TaskError(name='NonRetryableError', message='nothing to charge')
```

## Wait inside a task

`step.sleep(name, duration)` and `step.sleep_until(name, when)` pause the run
without holding the agent in memory. The SDK sets an alarm and continues the
run when it fires. The first deadline recorded for a name wins, so a replay
doesn't restart the timer.

```python
await step.sleep("wait for review", timedelta(hours=24))
```

`step.status(message)` records a progress message that shows up on the run as
`status_message`.

## Check on a run

```python
run = await self.build.get(run_id)
if run is not None and run.state == "completed":
    print(run.result)
```

`get()` returns `None` for an unknown id, a run of another task, or a run
started with `retain=False` that has settled. Otherwise it returns one of
these. Every run also has `run_id`, `definition`, `created_at` and `metadata`.

| `run.state`   | Enters it when                                                  | Class          | Extra fields                                                   |
| ------------- | --------------------------------------------------------------- | -------------- | -------------------------------------------------------------- |
| `"pending"`   | `run()` accepted it and no attempt has started.                 | `PendingRun`   |                                                                |
| `"running"`   | An attempt is executing.                                        | `RunningRun`   | `attempt`, `started_at`, `status_message`                      |
| `"waiting"`   | It reached a `step.sleep` or a step retry delay.                | `WaitingRun`   | `reason` (`"sleep"` or `"retry"`), `wake_at`, `status_message` |
| `"completed"` | The method returned. Final.                                     | `CompletedRun` | `result`, `settled_at`                                         |
| `"failed"`    | A step ran out of attempts, or code outside a step raised. Final. | `FailedRun`    | `error` (`name` and `message`), `settled_at`                   |
| `"cancelled"` | `cancel()` stopped it. Final.                                   | `CancelledRun` | `reason`, `settled_at`                                         |

`self.build.get_by_idempotency_key(key)` looks a run up by its key.
`self.tasks.list(definition=None, status=None, limit=100)` returns runs of
every task, newest first. Settled runs stay stored until you delete them with
`self.tasks.delete(status=None, settled_before=None, limit=100)`, which
returns how many it deleted.

## Cancel a run

```python
cancelled = await self.build.cancel(run_id, "customer cancelled")
```

A `pending` or `waiting` run becomes `cancelled` at once. A `running` one
becomes `cancelled` at its next step boundary, and the running step gets
`asyncio.CancelledError`. `cancel()` returns `False` when the run doesn't
exist, belongs to another task, or has already settled.
