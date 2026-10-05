---
title: Examples
---

# Examples

Short, self-contained patterns. Each one assumes the Wrangler setup from
[Getting started](getting-started.md), with the binding name changed to match.
The official [Containers examples](https://developers.cloudflare.com/containers/examples/)
cover the same scenarios in TypeScript.

## Environment variables and secrets

Values that are the same for every instance go in `env_vars`. Secrets and other
values from your Worker's environment can be read in `__init__`, because
`self.env` holds your
[secrets](https://developers.cloudflare.com/workers/configuration/secrets/)
and bindings:

```python
from typing import Any

from containers import Container, get_container
from workers import Request, Response, WorkerEntrypoint


class MyContainer(Container):
    default_port = 8080

    def __init__(self, ctx: Any, env: Any) -> None:
        super().__init__(ctx, env)
        self.env_vars = {
            "LOG_LEVEL": "info",
            "API_KEY": env.API_KEY,  # set with `npx wrangler secret put API_KEY`
        }
```

For values that differ per instance, pass `env_vars` when starting it. These
replace the class-level `env_vars` for that start, rather than merging with
them. They only take effect when the container actually starts. If it's
already running, they're ignored:

```python
class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        tenant = request.headers.get("x-tenant", "default")
        container = get_container(self.env.MY_CONTAINER, tenant)
        await container.start_and_wait_for_ports(
            env_vars={"LOG_LEVEL": "info", "TENANT": tenant},
        )
        return await container.fetch(request)
```

See [Environment variables](https://developers.cloudflare.com/containers/configuration/environment-variables/)
for the variables the platform sets for you.

## Monitor the container lifecycle

Use the hooks to log transitions, and expose `get_state()` for debugging:

```python
import json
from urllib.parse import urlparse

from containers import Container, State, get_container
from workers import Request, Response, WorkerEntrypoint


class MyContainer(Container):
    default_port = 8080
    sleep_after = "2m"

    async def on_start(self) -> None:
        print("container started")

    async def on_stop(self, *, exit_code: int, reason: str) -> None:
        print(f"container stopped: exit_code={exit_code} reason={reason}")

    async def on_error(self, error: Exception) -> None:
        print("container error:", error)
        raise error


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        url = urlparse(request.url)
        container = get_container(self.env.MY_CONTAINER, "monitored")

        if url.path == "/status":
            state: State = await container.get_state()
            return Response(json.dumps(state, indent=2))
        if url.path == "/stop":
            await container.stop()
            return Response("stopping")

        return await container.fetch(request)
```

## Run a job on a schedule

A [Cron Trigger](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
can start a container that does its work and exits. `start()` returns as soon
as the instance is up, without waiting for any port, so the image doesn't need
to serve HTTP:

```python
from datetime import date
from typing import Any

from containers import Container, get_container
from workers import WorkerEntrypoint


class NightlyReport(Container):
    entrypoint = ["python", "report.py"]
    enable_internet = False

    async def on_stop(self, *, exit_code: int, reason: str) -> None:
        if exit_code != 0:
            print(f"report failed with exit code {exit_code}")


class Default(WorkerEntrypoint):
    async def scheduled(self, controller: Any, env: Any, ctx: Any) -> None:
        container = get_container(self.env.NIGHTLY_REPORT, "nightly-report")
        await container.start(env_vars={"REPORT_DATE": date.today().isoformat()})
```

```jsonc title="wrangler.jsonc"
{
  // ...
  "triggers": { "crons": ["0 3 * * *"] }
}
```

See [Cron container](https://developers.cloudflare.com/containers/examples/cron/).

## Recurring work inside the Durable Object

`schedule()` runs one of your methods later, by name. To repeat it, schedule
the next run from inside the callback. Callbacks are `async def` methods that
take `(payload, schedule)`. A `TypedDict` keeps the payload typed from end to
end:

```python
from typing import TypedDict

from containers import Container, Schedule


class HealthPayload(TypedDict):
    interval: int


class MyContainer(Container):
    default_port = 8080

    async def on_start(self) -> None:
        # on_start can run more than once, so don't stack up duplicate tasks
        if not await self.list_schedules("health_report"):
            payload: HealthPayload = {"interval": 60}
            await self.schedule(60, "health_report", payload)

    async def health_report(
        self, payload: HealthPayload, schedule: Schedule[HealthPayload]
    ) -> None:
        state = await self.get_state()
        print("health:", state["status"])
        if state["status"] == "healthy":
            await self.schedule(payload["interval"], "health_report", payload)
```

`when` is either a number of seconds or a `datetime`. Payloads must be JSON
serialisable. Don't override `alarm()`, because the `Container` class uses it
for its own lifecycle management. See [Scheduling](container-class.md#scheduling).

## WebSockets

Forward the upgrade request with the stub's `fetch()`. The `Container` class
proxies messages in both directions and keeps the container awake while the
socket is open:

```python
from containers import get_container
from workers import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        if request.headers.get("upgrade", "").lower() == "websocket":
            container = get_container(self.env.MY_CONTAINER, "chat-room-1")
            return await container.fetch(request)
        return Response("expected a WebSocket upgrade", status=426)
```

Use `container.fetch(switch_port(request, port))` to reach a port other than
`default_port`. Don't use `container_fetch` from the Worker for WebSockets. See
[Sending requests](routing.md#sending-requests).

## Stateless, load-balanced instances

For interchangeable workers, spread requests across a fixed number of
instances:

```python
from containers import Container, get_random
from workers import Request, Response, WorkerEntrypoint


class Renderer(Container):
    default_port = 8080
    sleep_after = "5m"


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        container = await get_random(self.env.RENDERER, 5)
        return await container.fetch(request)
```

## Multiple ports

List every port your application needs in `required_ports`.
`start_and_wait_for_ports()` with no arguments waits for all of them, while a
request through `fetch()` waits only for the port it targets. `switch_port`
picks which port a request goes to:

```python
from urllib.parse import urlparse

from containers import Container, get_container, switch_port
from workers import Request, Response, WorkerEntrypoint


class App(Container):
    default_port = 8080  # HTTP API
    required_ports = [8080, 9090]  # API and metrics


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        container = get_container(self.env.APP, "main")
        if urlparse(request.url).path.startswith("/metrics"):
            return await container.fetch(switch_port(request, 9090))
        return await container.fetch(request)
```

## Sandboxed code with a mocked API

Block the internet and answer one API from the Worker, for example to run
untrusted or test code against a fake service:

```python
from typing import Any

from containers import (  # noqa: F401
    Container,
    ContainerProxy,
    OutboundHandlerContext,
    get_container,
)
from workers import Request, Response


async def fake_weather(
    request: Request, env: Any, ctx: OutboundHandlerContext
) -> Response:
    return Response.from_json({"city": "Lisbon", "temp_c": 21})


class Sandbox(Container):
    default_port = 8080
    enable_internet = False
    outbound_by_host = {"api.weather.example": fake_weather}
```

See [Outbound traffic](outbound-traffic.md) for host lists, HTTPS, and changing
rules at runtime.
