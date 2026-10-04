---
title: Routing and scaling
---

# Routing and scaling

Every container instance belongs to one Durable Object, and every Durable
Object has an ID. To route a request, you pick an ID, get a stub for that
Durable Object, and call it. `containers-py` has helpers for the common ways
of picking an ID.

For how the platform places and scales instances, see
[Scaling and Routing](https://developers.cloudflare.com/containers/configuration/scaling-and-routing/)
and [Limits and Instance Types](https://developers.cloudflare.com/containers/platform/limits/).

## Getting a stub

### One container per entity

`get_container(binding, name)` returns the stub for the instance called `name`,
creating it on first use. The same name always reaches the same instance, so
this suits containers tied to a user, session, document or game room:

```python
from containers import get_container

container = get_container(self.env.MY_CONTAINER, f"user-{user_id}")
return await container.fetch(request)
```

`get_container` is a plain function, so don't `await` it. Only the calls you
make on the stub are awaited.

### A single shared container

With no name, `get_container` returns one shared instance, named
`"cf-singleton-container"`:

```python
container = get_container(self.env.MY_CONTAINER)
```

### Stateless instances

For interchangeable instances, `get_random(binding, instances=3)` picks one of
`instances` named instances at random:

```python
from containers import get_random

container = await get_random(self.env.MY_CONTAINER, 5)
return await container.fetch(request)
```

Unlike `get_container`, `get_random` is a coroutine. It spreads load randomly,
without health or latency awareness. Keep `instances` at or below
`max_instances` in your Wrangler config. See
[Stateless instances](https://developers.cloudflare.com/containers/examples/stateless/)
for the platform side of this pattern.

## Sending requests

### `fetch`: the default port

`await stub.fetch(request)` calls the Durable Object's `fetch()` handler. That
handler starts the container if needed and forwards the request to
`default_port`. HTTP and WebSocket requests both work.

```python
return await container.fetch(request)
```

### `switch_port`: another port

To reach a port other than `default_port`, rewrite the request with
`switch_port`. It sets the `cf-container-target-port` header, which the
`Container` class reads:

```python
from containers import switch_port

return await container.fetch(switch_port(request, 9090))
```

### `container_fetch`: requests built in Python

`container_fetch` sends a request straight to the container, starting it first
if needed. It accepts either a `Request` or a URL. With a URL, keyword
arguments are the same as `workers.Request` (`method`, `headers`, `body` and so
on), and relative URLs are fine:

```python
response = await container.container_fetch(
    "/api/jobs",
    port=9090,
    method="POST",
    headers={"content-type": "application/json"},
    body=json.dumps({"input": "s3://bucket/video.mp4"}),
)
```

Passing request options together with a `Request` raises `TypeError`.

!!! warning "WebSockets"

    Called from your Worker, `container_fetch` is an
    [RPC](https://developers.cloudflare.com/workers/runtime-apis/rpc/) call, and
    WebSocket upgrades can't cross RPC yet
    ([workerd#2319](https://github.com/cloudflare/workerd/issues/2319)). For
    WebSockets, use `stub.fetch(...)`, combined with `switch_port` when you need
    another port.

## Calling container methods from a Worker

Any public `async def` method on your `Container` subclass, including the
SDK's own, can be called through the stub over
[RPC](https://developers.cloudflare.com/workers/runtime-apis/rpc/):

```python
container = get_container(self.env.MY_CONTAINER, "session-1")

state = await container.get_state()
await container.start_and_wait_for_ports(env_vars={"MODE": "batch"})
await container.stop("SIGINT")
await container.destroy()
```

JavaScript RPC has no keyword arguments, which affects what you can pass:

- `start`, `start_and_wait_for_ports`, `wait_for_port` and `container_fetch`
  accept keyword arguments over RPC. The SDK unpacks them on the other side.
- Every other method, including any you define yourself, should be called
  with positional arguments only.
- Arguments and return values must be serialisable. An `asyncio.Event` can't
  cross RPC, so the `abort=` and `signal=` cancellation arguments only work
  when called from inside the Durable Object.

### Adding your own methods

Define extra methods on your subclass to give your Worker a typed, narrow
interface instead of raw HTTP:

```python
class Transcoder(Container):
    default_port = 8080

    async def transcode(self, source_url, preset):
        response = await self.container_fetch(
            "/transcode",
            method="POST",
            body=json.dumps({"source": source_url, "preset": preset}),
        )
        return await response.json()


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        container = get_container(self.env.TRANSCODER, "video-123")
        result = await container.transcode("https://example.com/in.mp4", "720p")
        return Response.from_json(result)
```

## Customising `fetch` inside the container class

Override `fetch` on your subclass to add routing or authentication in front of
the container. Forward with `self.container_fetch`, not `self.fetch`, or the
call will recurse:

```python
class MyContainer(Container):
    default_port = 8080

    async def fetch(self, request):
        if urlparse(request.url).path == "/health":
            return Response("ok")

        if request.headers.get("authorization") != f"Bearer {self.env.API_TOKEN}":
            return Response("unauthorized", status=401)

        return await self.container_fetch(request)
```

## Type hints

`get_container` and `get_random` take a `DurableObjectNamespace` and return a
`DurableObjectStub`. Both are typing-only `Protocol`s exported from
`containers`, because the Workers runtime doesn't ship Python types for
bindings. Calls to methods on the stub are typed as `Any`.

```python
from containers import DurableObjectStub, get_container


def container_for(env, user_id: str) -> DurableObjectStub:
    return get_container(env.MY_CONTAINER, f"user-{user_id}")
```
