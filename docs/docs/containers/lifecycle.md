---
title: Lifecycle
---

# Lifecycle

A container starts on demand, serves requests, and goes to sleep once it has
been idle for `sleep_after`. The `Container` class tracks where each instance is
in that cycle and calls your hooks as it moves through it.

This page covers what the SDK does. For what the platform does underneath, such
as cold starts, placement and host restarts, see
[Lifecycle of a Container](https://developers.cloudflare.com/containers/concepts/architecture/).

## States

`await container.get_state()` returns a `State` dict:

```python
{"status": "healthy", "last_change": 1791100000000}
{"status": "stopped_with_code", "last_change": 1791100000000, "exit_code": 1}
```

| `status` | Meaning |
| --- | --- |
| `"stopped"` | Not running. Every instance starts here. |
| `"running"` | Started, but the SDK hasn't confirmed its ports are ready. `start()` leaves the container in this state. |
| `"healthy"` | Started, with every port it was waiting for accepting connections. |
| `"stopping"` | Shutting down. |
| `"stopped_with_code"` | Exited with a known exit code, stored in `exit_code`. |

`last_change` is a Unix timestamp in milliseconds. The state is persisted in the
Durable Object's storage, so it survives the Durable Object being evicted from
memory.

## Starting

You rarely need to start a container yourself. `fetch()` and
`container_fetch()` start it if it isn't `healthy`, wait for the target port,
then forward the request.

To start one ahead of a request, or without sending any traffic, call one of:

`start_and_wait_for_ports(ports=None)`
:   Starts the container and waits until every port is accepting connections.
    The state becomes `healthy`. If `ports` isn't given, it uses
    `required_ports`, then `default_port`.

`start()`
:   Starts the container and waits only until the instance is up. It doesn't
    wait for your application's ports. The state stays `running`. This suits
    batch jobs that don't serve HTTP.

```python
container = get_container(self.env.MY_CONTAINER, "job-42")
await container.start_and_wait_for_ports(
    env_vars={"JOB_ID": "42"},
    port_ready_timeout_ms=60_000,
)
```

Both accept `env_vars`, `entrypoint`, `enable_internet` and `labels`, which
override the class attributes of the same name for that start only. Startup
waits up to 8 seconds by default to get an instance, then up to 20 seconds
for ports, polling every 300 ms. See the
[`Container` reference](container-class.md#start-and-stop) for every option.

Concurrent requests that arrive while a container is starting share the same
start, so the container is never started twice.

## Lifecycle hooks

Override these `async def` methods on your subclass. They must be coroutines,
because the SDK always awaits them.

```python
class MyContainer(Container):
    default_port = 8080

    async def on_start(self):
        await self.schedule(60, "health_report")

    async def on_stop(self, *, exit_code, reason):
        print(f"stopped: exit_code={exit_code} reason={reason}")

    async def on_error(self, error):
        print("container error:", error)
        raise error

    async def on_activity_expired(self):
        print("idle, shutting down")
        await self.stop()
```

`on_start()`
:   Runs after a successful `start()` or `start_and_wait_for_ports()`, including
    the implicit start inside `fetch()`. Explicit calls run it even when the
    container was already running, so make it safe to run more than once. It
    runs inside
    [`block_concurrency_while`](https://developers.cloudflare.com/durable-objects/api/state/#blockconcurrencywhile),
    so no other event reaches this Durable Object until it returns. Keep it
    short.

`on_stop(*, exit_code, reason)`
:   Runs once the SDK sees the container process has exited, whether it was
    stopped, destroyed, crashed or exited on its own. `exit_code` is an `int`,
    `0` for a clean exit. A `destroy()` usually reports `137` (128 + `SIGKILL`).

`on_error(error)`
:   Runs when starting the container or waiting for a port fails. `error` is
    always an `Exception`. The default implementation logs it and re-raises.
    Errors raised from this hook are ignored, and the original error propagates
    anyway.

`on_activity_expired()`
:   Runs when the container has been idle for `sleep_after`. The default
    implementation calls `self.stop()`. See [Sleeping](#sleeping).

## Sleeping

`sleep_after` sets how long a container can sit idle before
`on_activity_expired()` runs. It accepts seconds as an `int`, or a string such as
`"30s"`, `"5m"` or `"2h"`. The default is `"10m"`.

```python
class MyContainer(Container):
    sleep_after = "5m"
```

These all count as activity and reset the timer:

- a request proxied through `fetch()` or `container_fetch()`,
- a response body that is still streaming,
- an open WebSocket, plus every message sent through it in either direction,
- starting the container.

The timer only starts counting once the last in-flight request has finished, so
a long download or a WebSocket that stays open keeps the container awake.

If the container does work that doesn't go through the SDK, for example a
background job it started itself, call `self.renew_activity_timeout()` to keep
it awake. This is a plain method, not a coroutine.

!!! warning "Overriding `on_activity_expired`"

    If you override it, call `await self.stop()` or `await self.destroy()`
    yourself, otherwise the container never goes to sleep. When the hook
    returns without stopping, the SDK renews the timeout and calls it again
    after another `sleep_after`.

The SDK checks the timer from the Durable Object's
[alarm](https://developers.cloudflare.com/durable-objects/api/alarms/). The
`Container` class owns the `alarm()` handler, so don't override it. Use
[`schedule()`](container-class.md#scheduling) for your own timed work.

## Stopping

`await container.stop(signal="SIGTERM")`
:   Sends a signal to the container's main process. It takes `"SIGTERM"`,
    `"SIGINT"`, `"SIGKILL"`, an `int`, or a
    [`signal.Signals`](https://docs.python.org/3/library/signal.html#signal.Signals)
    member. With `SIGTERM`, your process can shut down cleanly.

`await container.destroy()`
:   Kills the container immediately with `SIGKILL`.

Both lead to `on_stop()`. When the platform itself stops a container, such as
during a [rollout](https://developers.cloudflare.com/containers/configuration/rollouts/),
it sends `SIGTERM` first and follows with `SIGKILL` after a grace period. Handle
`SIGTERM` in your image if you need to clean up.

## Persistence

Container disk is ephemeral. Every start begins from a fresh copy of the image.
To keep data across restarts, store it in the Durable Object's own storage, for
example through `self.ctx.storage`, or in another service such as
[R2](https://developers.cloudflare.com/r2/). See
[SQLite storage](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
and [Mount R2 buckets with FUSE](https://developers.cloudflare.com/containers/examples/r2-fuse-mount/).

## Errors from requests

When `fetch()` or `container_fetch()` can't reach the container, it returns a
`Response` instead of raising:

| Status | Cause |
| --- | --- |
| `503` | No instance is available. You may have reached `max_instances`, or a first deploy may still be provisioning. |
| `429` | Containers are being requested too quickly. |
| `500` | The container failed to start, disconnected mid-request (`"Container suddenly disconnected, try again"`), or the request couldn't be proxied. |

`start()` and `start_and_wait_for_ports()` raise instead, usually a
`RuntimeError`, after calling `on_error()`.
