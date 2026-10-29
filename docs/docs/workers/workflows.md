---
title: "Write a Cloudflare Workflow in Python"
description: "Define Workflow steps with crosswind-workers, pass results between steps, configure retries, stop retries with NonRetryableError, sleep, wait for events and start instances."
---

A [Workflow](https://developers.cloudflare.com/workflows/) runs a series of
steps. The runtime saves each step's result, retries a step that raises, and
can pause between steps for minutes or days. In crosswind-workers you write
the whole Workflow in Python.

## Define the Workflow

=== "wrangler.jsonc"

    ```jsonc
    {
      "name": "signup",
      "main": "src/entry.py",
      "compatibility_date": "2026-09-08",
      "compatibility_flags": ["python_workers"],
      "workflows": [{ "name": "signup", "binding": "SIGNUP", "class_name": "Signup" }]
    }
    ```

=== "wrangler.toml"

    ```toml
    name = "signup"
    main = "src/entry.py"
    compatibility_date = "2026-09-08"
    compatibility_flags = ["python_workers"]

    [[workflows]]
    name = "signup"
    binding = "SIGNUP"
    class_name = "Signup"
    ```

=== "cloudflare.config.py"

    ```python
    from wynd.config import Config, Worker, bindings, exports

    config = Config(
        worker=Worker(
            name="signup",
            entrypoint="src/entry.py",
            compatibility_date="2026-09-08",
            env={"SIGNUP": bindings.Workflow("Signup")},
            exports={"Signup": exports.Workflow(name="signup")},
        ),
    )
    ```

```python title="src/entry.py"
from urllib.parse import parse_qs, urlparse

from env import Env
from workers import Request, Response, WorkerEntrypoint, WorkflowEntrypoint
from workers.workflows import NonRetryableError

RETRIES = {"retries": {"limit": 3, "delay": "5 seconds", "backoff": "exponential"}}


class Signup(WorkflowEntrypoint[Env]):
    async def run(self, event, step):
        email = event["payload"]["email"]

        @step.do("validate")
        async def validate():
            if "@" not in email:
                raise NonRetryableError(f"not an email address: {email}")
            return email.lower()

        @step.do("send welcome", config=RETRIES)
        async def send_welcome(validate):
            return f"welcome mail sent to {validate}"

        await step.sleep("cool down", "1 second")
        return await send_welcome()


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        url = urlparse(request.url)
        if url.path == "/start":
            email = parse_qs(url.query)["email"][0]
            instance = await self.env.SIGNUP.create(params={"email": email})
            return Response(instance.id)

        instance = await self.env.SIGNUP.get(parse_qs(url.query)["id"][0])
        return Response.json(await instance.status())
```

Run it with `uvx wynd dev`, start an instance, then check its status:

```sh
curl 'http://localhost:8787/start?email=Ada@Example.com'
# 6c1f4b0e-...
curl 'http://localhost:8787/status?id=6c1f4b0e-...'
# {"status": "complete", "__LOCAL_DEV_STEP_OUTPUTS": ["ada@example.com", "welcome mail sent to ada@example.com"], "output": "welcome mail sent to ada@example.com", "error": null}
```

`__LOCAL_DEV_STEP_OUTPUTS` appears only under `wynd dev`. `event["payload"]`
holds the `params` you passed to `create`.

Python Workflows need the `python_workflows` compatibility flag. workerd turns
it on for compatibility dates from 2025-09-20, so the config above doesn't
list it. On an earlier date, add `"python_workflows"` to
`compatibility_flags`.

To see a step fail, start an instance with `?email=nope`. The status is
`errored` after one attempt, because `NonRetryableError` skips the retries:

```json
{"status": "errored", "output": null, "error": {"name": "Error", "message": "PythonError: Traceback (most recent call last): ... workers.workflows.NonRetryableError: not an email address: nope\n"}}
```

The last line of `error["message"]` names the exception that ended `run`.

## Steps

`step.do(name)` turns a function into a step and returns an `async` callable.
The step runs when you await that callable, and the runtime saves its return
value. If
the Workflow restarts, a finished step returns its saved result without
running again. A step's result must cross RPC, so return a `dict`, `list`,
`str`, number or `None`. A `tuple` fails the step with
`TypeError: <class 'tuple'> cannot be sent over RPC.`

A step that needs another step's result takes a parameter named after that
step's function. Awaiting `send_welcome()` above runs `validate` first and
passes its result in as `validate`. A step runs once per Workflow run, however
many steps depend on it. A parameter named `ctx` receives the step's context.

This form needs the `python_workflows_implicit_dependencies` compatibility
flag. workerd turns it on for compatibility dates from 2026-02-25, so it is on
at the 2026-09-08 date this page uses. On an earlier date without the flag,
list the dependencies in `depends=`, in the order of the parameters. The
parameter names then don't matter:

```python
@step.do("send welcome", depends=[validate], config=RETRIES)
async def send_welcome(address):
    return f"welcome mail sent to {address}"
```

Passing `depends` while the flag is on raises
`TypeError: depends is not accepted with python_workflows_implicit_dependencies`.

`concurrent=True` runs a step's dependencies at the same time instead of one
after another.

## Retries

`config` takes the same dictionary as the JavaScript API: `retries` with
`limit`, `delay` and `backoff`, and `timeout`. The runtime retries a step that
raises any exception up to `limit` times, so `limit: 2` means 3 attempts in
all. Once the retries run out, awaiting the step in `run` raises `TypeError`
or `ValueError` as their own classes. Other exception classes arrive as
`pyodide.ffi.JsException`.

Raise [`NonRetryableError`](api.md#workflows) to fail the step at once. `run`
receives the same exception class when it awaits the step, so you can catch
it there:

```python
try:
    await validate()
except NonRetryableError as error:
    return {"rejected": str(error)}
```

## Sleep and wait for events

```python
from datetime import UTC, datetime

await step.sleep("cool down", "1 hour")
await step.sleep_until("launch", datetime(2027, 1, 1, tzinfo=UTC))
approval = await step.wait_for_event("approval", "approved", timeout="2 days")
```

`wait_for_event` returns a dictionary with `type`, `timestamp` and your data
under `payload`. `timeout` defaults to `"24 hours"`. With no matching event
before `timeout`, the step fails. Send the event from a Worker:

```python
instance = await self.env.SIGNUP.get(instance_id)
await instance.send_event(type="approved", payload={"by": "ada"})
```

## Control instances

[`Workflow`](api.md#workflow) creates and finds instances, and
[`WorkflowInstance`](api.md#workflowinstance) has `status`, `pause`, `resume`,
`restart` and `terminate`.

### Instance status

`await instance.status()` returns a dictionary whose `status` is one of these
values, from the runtime's `InstanceStatus` type:

| `status`          | What it means                                                       | What to do                                          |
| ----------------- | ------------------------------------------------------------------- | --------------------------------------------------- |
| `queued`          | Created, and waiting for the runtime to start it.                   | Poll again.                                         |
| `running`         | A step is running.                                                  | Poll again.                                         |
| `waiting`         | Asleep in `step.sleep` or `step.sleep_until`, or in `wait_for_event`. | Send the event, or wait for the sleep to end.     |
| `paused`          | Stopped by `pause()`.                                               | Call `resume()`.                                    |
| `waitingForPause` | Finishing its current step before it pauses.                        | Poll until it reads `paused`.                       |
| `complete`        | `run` returned. Its return value is in `output`.                    | Read `output`.                                      |
| `errored`         | `run` raised, or a step ran out of retries.                         | Read `error["message"]`, then `restart()` if the cause is fixed. |
| `terminated`      | Stopped by `terminate()`.                                           | Create a new instance.                              |
| `unknown`         | None of the statuses above.                                         | Poll again.                                         |

`output` is `None` until the instance completes, and `error` is `None` unless
it errored.
