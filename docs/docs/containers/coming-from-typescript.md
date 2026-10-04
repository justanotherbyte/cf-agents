---
title: Coming from TypeScript
---

# Coming from TypeScript

`containers-py` ports
[`@cloudflare/containers`](https://github.com/cloudflare/containers) v0.3.7.
The behaviour is the same, including defaults, timeouts, state transitions and
outbound rule precedence, so the official
[Containers docs](https://developers.cloudflare.com/containers/) apply as
written. The differences are in how the API is shaped, which this page maps
out.

## Side by side

=== "Python"

    ```python
    from containers import Container, get_container
    from workers import WorkerEntrypoint


    class MyContainer(Container):
        default_port = 4000
        sleep_after = "10m"


    class Default(WorkerEntrypoint):
        async def fetch(self, request):
            data = await request.json()
            container = get_container(self.env.MY_CONTAINER, data["session-id"])
            return await container.fetch(request)
    ```

=== "TypeScript"

    ```typescript
    import { Container, getContainer } from "@cloudflare/containers";

    export class MyContainer extends Container {
      defaultPort = 4000;
      sleepAfter = "10m";
    }

    export default {
      async fetch(request, env) {
        const { "session-id": sessionId } = await request.json();
        const container = getContainer(env.MY_CONTAINER, sessionId);
        return container.fetch(request);
      },
    };
    ```

## Naming

Names are converted to `snake_case`, and fields become class attributes.

| TypeScript | Python |
| --- | --- |
| `defaultPort`, `requiredPorts`, `sleepAfter` | `default_port`, `required_ports`, `sleep_after` |
| `envVars`, `entrypoint`, `enableInternet` | `env_vars`, `entrypoint`, `enable_internet` |
| `pingEndpoint`, `interceptHttps` | `ping_endpoint`, `intercept_https` |
| `allowedHosts`, `deniedHosts` | `allowed_hosts`, `denied_hosts` |
| `static outboundByHost`, `static outbound`, `static outboundHandlers` | `outbound_by_host`, `outbound`, `outbound_handlers` |
| `onStart`, `onStop`, `onError`, `onActivityExpired` | `on_start`, `on_stop`, `on_error`, `on_activity_expired` |
| `containerFetch`, `startAndWaitForPorts`, `waitForPort` | `container_fetch`, `start_and_wait_for_ports`, `wait_for_port` |
| `getState`, `renewActivityTimeout` | `get_state`, `renew_activity_timeout` |
| `getContainer`, `getRandom`, `switchPort` | `get_container`, `get_random`, `switch_port` |
| `State.lastChange`, `State.exitCode` | `last_change`, `exit_code` |
| `Schedule.taskId`, `Schedule.delayInSeconds` | `task_id`, `delay_in_seconds` |
| `ctx.containerId`, `ctx.className` (outbound) | `ctx.container_id`, `ctx.class_name` |
| `export { ContainerProxy } from "@cloudflare/containers"` | `from containers import ContainerProxy  # noqa: F401` |

## Options objects become keyword arguments

Nested option objects are flattened into keyword-only arguments.

| TypeScript | Python |
| --- | --- |
| `startAndWaitForPorts({ ports: 8080, startOptions: { envVars }, cancellationOptions: { portReadyTimeoutMS, abort } })` | `start_and_wait_for_ports(8080, env_vars=..., port_ready_timeout_ms=..., abort=...)` |
| `startAndWaitForPorts(8080, { portReadyTimeoutMS: 30_000 }, { envVars })` | `start_and_wait_for_ports(8080, port_ready_timeout_ms=30_000, env_vars=...)` |
| `start({ envVars, entrypoint }, { portToCheck, retries })` | `start(env_vars=..., entrypoint=..., port_to_check=..., retries=...)` |
| `waitForPort({ portToCheck: 8080, retries: 10 })` | `wait_for_port(8080, retries=10)` |
| `containerFetch("/api", { method: "POST" }, 9090)` | `container_fetch("/api", port=9090, method="POST")` |
| `onStop({ exitCode, reason })` | `on_stop(self, *, exit_code, reason)` |
| `new Container(ctx, env, { defaultPort: 8080 })` | `super().__init__(ctx, env, default_port=8080)` |

Units stay the same as in TypeScript: timeouts and `wait_interval` are in
milliseconds, while `sleep_after` and `schedule()` delays are in seconds.

## Cancellation

`AbortSignal` is replaced by
[`asyncio.Event`](https://docs.python.org/3/library/asyncio-sync.html#asyncio.Event).
Set the event to cancel:

```python
abort = asyncio.Event()
task = asyncio.ensure_future(self.start_and_wait_for_ports(abort=abort))
# later
abort.set()
```

Cancellation is checked at the same points as in TypeScript, so a start is
never interrupted halfway through a state transition. An event can't cross RPC,
so this only works inside the Durable Object.

## Behaviour differences

**Hooks and callbacks must be `async def`.**
`on_start`, `on_stop`, `on_error`, `on_activity_expired`, outbound handlers and
`schedule()` callbacks are always awaited.

**`on_error` always receives an exception.**
In a few paths, TypeScript passes a string or another non-`Error` value. Python
wraps these in `RuntimeError`.

**Subclasses inherit outbound configuration.**
`outbound`, `outbound_by_host` and `outbound_handlers` are looked up as
ordinary class attributes, so a subclass inherits its parent's handlers.
TypeScript keys them by exact class name, so subclasses don't inherit them
there.

**Keyword arguments over RPC.**
JavaScript RPC has no keyword arguments. `start`, `start_and_wait_for_ports`,
`wait_for_port` and `container_fetch` accept them anyway. Call every other
method on a stub positionally. See
[Calling container methods](routing.md#calling-container-methods-from-a-worker).

**Errors.**
`new Error(...)` becomes `RuntimeError`, and invalid arguments raise
`ValueError` or `TypeError`. The original error is chained as `__cause__`.

**Logging.**
`console.log`, `console.warn` and `console.error` become `logger.info`,
`logger.warning` and `logger.error` on the `containers.container` logger.

## Not ported

| TypeScript | Use instead |
| --- | --- |
| `loadBalance` (deprecated) | `get_random` |
| `static outboundProxies` / `outboundProxy` aliases | `outbound_by_host` / `outbound` |
| `outboundParams` (typing helper) | Plain values for `params` |
| `ContainerOptions.id` (unused) | n/a |

## Raw container API

Like the TypeScript class, `Container` is built on the runtime's
[Durable Object Container API](https://developers.cloudflare.com/containers/api/durable-object-container/),
which you can reach at `self.ctx.container`. It's a JavaScript object accessed
through Pyodide's [FFI](https://developers.cloudflare.com/workers/languages/python/ffi),
so it uses `camelCase` method names. Prefer the `Container` methods, which keep
the SDK's state tracking in sync.
