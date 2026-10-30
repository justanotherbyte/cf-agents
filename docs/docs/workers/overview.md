---
title: "crosswind-workers: a typed Python Workers SDK"
description: "What crosswind-workers is, how it relates to workers-runtime-sdk, and when to use it in a Python Worker."
---

Use `crosswind-workers` for a
[Python Worker](https://developers.cloudflare.com/workers/languages/python/)
built with Wynd. It replaces Cloudflare's `workers-runtime-sdk` and imports
as `workers`. It keeps every public name and call signature from the stock
SDK while adding types and Python methods. pywrangler always installs
`workers-runtime-sdk`, so projects built with it must use that SDK.

```python
from env import Env
from workers import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        await self.env.CACHE.put("last-path", request.url, expiration_ttl=3600)
        return Response.json({"cached": await self.env.CACHE.get("last-path")})
```

`Env` is a class that [Wynd generates](../wynd/types.md) from your Wrangler
config. With it, your editor knows that `self.env.CACHE` is a
[`KVNamespace`](api.md#kvnamespace) and that `get` returns `str | None`.

## How it relates to the stock SDK

Python Workers import a package called `workers` at startup. Cloudflare's
`workers-runtime-sdk` provides it, and so does `crosswind-workers`. A project
ships exactly one of them.

Code written for the stock SDK runs on crosswind-workers without changes:

- Every name in the stock SDK's `workers` module exists, with the same
  signature.
- camelCase JavaScript calls such as `self.ctx.storage.deleteAll()` still
  work. crosswind forwards any name its classes don't define to the
  JavaScript object.
- `asgi` and `wsgi` adapters serve FastAPI, Flask and other frameworks the
  same way.

crosswind-workers adds:

- `WorkerEntrypoint[Env]`, `DurableObject[Env]` and `WorkflowEntrypoint[Env]`,
  so `self.env` has a type.
- [Binding classes](bindings.md) for KV, D1, R2, Queues, Durable Objects,
  service bindings, Workflows, Vectorize, Analytics Engine, Images and rate
  limiting, with snake_case methods that take and return Python values.
- `js_object` on every wrapper, which returns the JavaScript object
  underneath. See [Call JavaScript APIs](javascript.md).
- Workflows written in Python only, with no JavaScript helper file in the
  package.
- Fixes for 14 stock SDK bugs. [Migrate from
  workers-runtime-sdk](migrate.md#differences-from-the-stock-sdk) lists
  them.

## Install

Add the package to a Python Worker project that you build with Wynd:

```sh
uv add crosswind-workers
```

If the project lists `workers-runtime-sdk`, remove it first with
`uv remove workers-runtime-sdk`. To create a project first, follow
[Getting started](getting-started.md). To move an existing project, follow
[Migrate from workers-runtime-sdk](migrate.md).

## Requirements

- Python 3.12, 3.13 or 3.14 as the Worker's runtime. The
  [target](../wynd/commands.md#python-targets) comes from your compatibility
  date and flags.
- [Wynd](../wynd/overview.md) to build and deploy the Worker. pywrangler always
  installs `workers-runtime-sdk` next to whatever SDK you declare. Both
  packages write to `python_modules/workers/`, so crosswind-workers refuses to
  import when it finds the stock SDK installed.

The package has no runtime dependencies.

## Status

`crosswind-workers` is pre-1.0, and its API may change between minor releases.
It is an independent community project, not an official Cloudflare SDK.
These parts of the runtime have no typed API yet:

- Module-level `on_fetch` handlers. The `handler` decorator works as in the
  stock SDK.
- Streaming request and response bodies beyond the `body` stream property.
- A typed WebSocket API for Durable Objects. Handlers receive the JavaScript
  `WebSocket`.

## Next steps

- [Getting started](getting-started.md) builds a Worker with a Durable Object
  and runs it locally.
- [Use bindings](bindings.md) covers KV, D1, R2, Queues and service bindings.
- [API reference](api.md) lists every class and method.
