---
title: "Start, stop and put Python containers to sleep"
description: "Start containers before traffic arrives, run code on start and stop with lifecycle hooks, control when idle containers sleep, and stop them yourself."
---

A container starts on its first request, serves traffic, and stops once it
has been idle for `sleep_after`. This guide shows how to change each of those
steps. For what the platform does underneath, such as placement and host
restarts, see
[Lifecycle of a Container](https://developers.cloudflare.com/containers/concepts/architecture/).

## Check the state

`await container.get_state()` returns a `State` dict:

```python
from containers import State

state: State = await container.get_state()
# {"status": "healthy", "last_change": 1791100000000}
# {"status": "stopped_with_code", "last_change": 1791100000000, "exit_code": 1}
```

| `status`              | How it gets here                                                                                                   | What to do                                                       |
| --------------------- | ------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| `"stopped"`           | No state stored yet, a start ran out of attempts, or `on_stop()` returned.                                          | Nothing. The next request starts the container.                  |
| `"running"`           | The SDK called the runtime's start. `start()` ends here, and so does a start whose port check timed out.            | Call `start_and_wait_for_ports()` or send a request to reach `healthy`. |
| `"healthy"`           | `start_and_wait_for_ports()` got a response from every port it waited for.                                          | Send requests. `fetch()` skips the start path in this state.     |
| `"stopping"`          | Never. The type includes it, but the SDK doesn't set it.                                                            | Nothing.                                                         |
| `"stopped_with_code"` | The process exited and the runtime reported a code, stored in `exit_code`. A clean exit records `0`.                | Wait for `on_stop()`, which runs next and moves the state to `stopped`. |

`last_change` is a Unix timestamp in milliseconds. The SDK keeps the state in
Durable Object storage, so it survives the Durable Object leaving memory.

## Start a container before traffic arrives

`fetch()` and `container_fetch()` start the container when it isn't
`healthy`, so most Workers never start one explicitly. Start one yourself to
warm it up, to pass per-start settings, or to run a job that doesn't serve
HTTP.

`start_and_wait_for_ports()` starts the container and waits until its ports
accept connections. The state becomes `healthy`:

```python
container = get_container(self.env.MY_CONTAINER, "job-42")
await container.start_and_wait_for_ports(
    env_vars={"JOB_ID": "42"},
    port_ready_timeout_ms=60_000,
)
```

With no `ports` argument it waits for `required_ports`, or for `default_port`
when `required_ports` isn't set.

`start()` waits only until the instance is up, not for any port. The state
stays `running`. Use it for batch jobs:

```python
await container.start(env_vars={"REPORT_DATE": "2026-10-07"})
```

Both accept `env_vars`, `entrypoint`, `enable_internet` and `labels`, which
replace the class attributes of the same name for that start. These apply only
when the call starts the container. If it's already running, the SDK ignores
them.

Requests that arrive while a container is starting wait for the same start, so
the SDK never starts one container twice. See
[Start and stop](container-class.md#start-and-stop) for every option.

### Timeouts

The SDK counts timeouts in attempts, polling every 300 milliseconds
(`wait_interval`). Each attempt sends one readiness request that times out
after 5,000 milliseconds.

| Phase                 | Default budget                              | Option                    |
| --------------------- | ------------------------------------------- | ------------------------- |
| Get an instance       | 8,000 ms, so 27 attempts                    | `instance_get_timeout_ms` |
| Wait for the ports    | 20,000 ms, so 67 attempts, including the attempts the instance phase used | `port_ready_timeout_ms` |

### When a start fails or times out

Check `await container.get_state()` to see where a failed start left the
container:

| What happened                                 | What the call raises                                                                                                                    | State afterwards                   |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| No instance answered within the instance budget | `RuntimeError: there is no container instance that can be provided to this durable object`                                              | `"stopped"`                        |
| The process exited before the instance answered | `RuntimeError: Container exited before we could determine the container health, exit code: <n>`, or the runtime's own exit error       | `"stopped_with_code"` or `"stopped"` |
| The process exited while the SDK waited for a port | The last connection error. `on_error()` receives `Container crashed while checking for ports, ...`                                  | `"running"` until the SDK next checks the state, then `on_stop()` runs |
| A port didn't answer within the budget        | The last connection error. `on_error()` receives `Failed to verify port <port> is available after <n>ms, last error: ...`               | `"running"`                        |
| `abort` was set                               | `RuntimeError: Aborted waiting for container to start as we received a cancellation signal`, or `RuntimeError: Container request aborted.` while it waits for ports | `"running"`                        |

After a port timeout the container keeps running. The next request or
`start_and_wait_for_ports()` call waits for the port again without restarting
it. Call `stop()` or `destroy()` if you want it gone instead. `fetch()` and
`container_fetch()` turn these errors into responses, listed in
[Troubleshooting](troubleshooting.md#responses-from-fetch).

To see each case locally with `pywrangler dev`, set `default_port` to a port
your server doesn't listen on for a port timeout, or make the image's `CMD`
exit with `["sh", "-c", "exit 1"]` for an exit during start.

## Run code on lifecycle events

Override these `async def` methods on your subclass. The SDK awaits each one,
so a plain `def` fails.

```python
from containers import Container


class MyContainer(Container):
    default_port = 8080

    async def on_start(self) -> None:
        print("started")

    async def on_stop(self, *, exit_code: int, reason: str) -> None:
        print(f"stopped: exit_code={exit_code} reason={reason}")

    async def on_error(self, error: Exception) -> None:
        print("container error:", error)
        raise error
```

### `on_start()`

Runs after a successful `start()` or `start_and_wait_for_ports()`, including
the start inside `fetch()`. An explicit start call runs it even when the
container was already running, so make it safe to run twice. For example,
check `list_schedules()` before you schedule a task from it, or each call adds
another copy.

It runs inside
[`block_concurrency_while`](https://developers.cloudflare.com/durable-objects/api/state/#blockconcurrencywhile).
No other request reaches this Durable Object until it returns, so keep it
short.

### `on_stop(*, exit_code, reason)`

Runs after the container process exits, whatever the cause. The SDK calls it
when it next checks the state: on the next alarm, or on the next `stop()` or
`start_and_wait_for_ports()` call. `exit_code` is `0` for a clean exit.
`destroy()` usually reports `137` (128 + `SIGKILL`). `reason` is always
`"exit"` in this version, as in the TypeScript SDK. The type also allows
`"runtime_signal"`, but the SDK never passes it.

If `on_stop()` raises, the state doesn't move to `stopped`, and the SDK calls
the hook again on its next check.

### `on_error(error)`

Runs when starting the container or waiting for a port fails, and when the
runtime reports a container failure that has no exit code. The default logs
`error` and re-raises it. If your override raises a different error, the SDK
ignores it and raises the original one.

### `on_activity_expired()`

Runs when the container has been idle for `sleep_after`. The default calls
`self.stop()`. See the next section before you override it.

## Control when a container sleeps

`sleep_after` sets how long a container can sit idle. It takes seconds as an
`int`, or a string such as `"30s"`, `"5m"` or `"2h"`. The default is `"10m"`.

```python
class MyContainer(Container):
    sleep_after = "5m"
```

These reset the idle timer:

- a request through `fetch()` or `container_fetch()`,
- a response body that is still streaming,
- an open WebSocket, and each message sent through it,
- starting the container.

The timer starts counting only after the last request in flight finishes. A
long download or an open WebSocket keeps the container awake.

Work the SDK can't see, such as a background job the container started on its
own, doesn't reset the timer. Call `self.renew_activity_timeout()` from your
subclass while that work runs. It's a plain method, so don't await it.

> **Warning**
>
> If you override `on_activity_expired`, call `await self.stop()` or
> `await self.destroy()` in it. When the hook returns without stopping the
> container, the SDK renews the timer and calls the hook again after another
> `sleep_after`, so the container never sleeps.

The SDK checks the timer from the Durable Object
[alarm](https://developers.cloudflare.com/durable-objects/api/alarms/), so
don't override `alarm()`. Use [`schedule()`](container-class.md#scheduling)
for your own timed work.

## Stop a container

`await container.stop(signal="SIGTERM")` sends a signal to the container's
main process. It accepts `"SIGTERM"`, `"SIGINT"`, `"SIGKILL"`, an `int` or a
[`signal.Signals`](https://docs.python.org/3/library/signal.html#signal.Signals)
member. With `SIGTERM`, your process can shut down cleanly.

`await container.destroy()` kills the container with `SIGKILL`.

Both lead to `on_stop()`. When the platform stops a container itself, for
example during a
[rollout](https://developers.cloudflare.com/containers/configuration/rollouts/),
it sends `SIGTERM` and follows with `SIGKILL` after a grace period. Handle
`SIGTERM` in your image if it needs to clean up.

## Keep data across restarts

Container disk is ephemeral. Every start begins from a fresh copy of the image.
To keep data, write it to the Durable Object's storage through
`self.ctx.storage`, or to another service such as
[R2](https://developers.cloudflare.com/r2/). See
[Mount R2 buckets with FUSE](https://developers.cloudflare.com/containers/examples/r2-fuse-mount/).
