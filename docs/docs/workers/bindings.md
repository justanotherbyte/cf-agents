---
title: "Use KV, D1, R2, Queues and service bindings from Python"
description: "Read and write Workers bindings through crosswind-workers' typed classes, with Python values in and out, and fall back to JavaScript names when a method has no typed version."
---

A binding in your Wrangler config becomes an attribute of `self.env`.
crosswind-workers wraps each one in a class with `async` snake_case methods
that take and return Python values. Every example on this page uses this
config:

```jsonc title="wrangler.jsonc"
{
  "name": "bindings-demo",
  "main": "src/entry.py",
  "compatibility_date": "2026-09-08",
  "compatibility_flags": ["python_workers"],
  "kv_namespaces": [{ "binding": "CACHE", "id": "<your KV namespace id>" }],
  "d1_databases": [
    { "binding": "DB", "database_name": "notes", "database_id": "<your D1 database id>" }
  ],
  "r2_buckets": [{ "binding": "FILES", "bucket_name": "files" }],
  "queues": { "producers": [{ "binding": "JOBS", "queue": "jobs" }] }
}
```

`wynd dev` simulates every binding locally, so any placeholder id works until
you deploy. Create the real resources with `uvx wynd kv namespace create`,
`uvx wynd d1 create`, `uvx wynd r2 bucket create` and
`uvx wynd queues create`. Wynd passes these commands to Wrangler unchanged.

Each binding's class comes from the binding's JavaScript type at runtime:

| Config section               | Class                                                    |
| ---------------------------- | -------------------------------------------------------- |
| `kv_namespaces`              | [`KVNamespace`](api.md#kvnamespace)                       |
| `d1_databases`               | [`D1Database`](api.md#d1database)                         |
| `r2_buckets`                 | [`R2Bucket`](api.md#r2bucket)                             |
| `queues.producers`           | [`Queue`](api.md#queue)                                   |
| `durable_objects.bindings`   | [`DurableObjectNamespace`](api.md#durableobjectnamespace) |
| `services`, `assets`         | [`Fetcher`](api.md#fetcher)                               |
| `workflows`                  | [`Workflow`](api.md#workflow)                             |
| `vars` and secrets           | `str`, or a Python value for a JSON var                  |
| Anything else, such as `ai`  | [`Binding`](api.md#binding)                               |

Run `uvx wynd types` after you change the config, so `Env` in `src/env.py`
lists the new bindings. `wynd dev` and `wynd deploy` also regenerate it.

## KV

```python title="src/entry.py"
from env import Env
from workers import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        await self.env.CACHE.put("user:1", '{"name": "Ada"}', expiration_ttl=3600)
        text = await self.env.CACHE.get("user:1")  # str, or None if missing
        user = await self.env.CACHE.get("user:1", "json")  # parsed into a dict
        listing = await self.env.CACHE.list(prefix="user:")
        return Response.json(
            {"text": text, "name": user["name"], "keys": len(listing["keys"])}
        )
```

```json
{"text": "{\"name\": \"Ada\"}", "name": "Ada", "keys": 1}
```

KV rejects an `expiration_ttl` below 60 seconds. The second argument of `get`
picks the type. `"text"` is the default, `"json"` parses the value,
`"arrayBuffer"` returns a `memoryview` and `"stream"` returns a stream.

## D1

```python title="src/entry.py"
from env import Env
from workers import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        db = self.env.DB
        await db.exec("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT)")
        await db.prepare("INSERT INTO notes (body) VALUES (?)").bind("first").run()

        result = await db.prepare("SELECT id, body FROM notes").all()
        first = await db.prepare("SELECT body FROM notes WHERE id = ?").bind(1).first("body")
        return Response.json({"rows": result["results"], "first": first})
```

On the first request:

```json
{"rows": [{"id": 1, "body": "first"}], "first": "first"}
```

`bind` returns a new statement, so you can prepare a query once and bind it
many times. `batch` runs a list of bound statements in one transaction. A
dictionary from D1 also reads by attribute, so `result.results` works.

## R2

```python title="src/entry.py"
from env import Env
from workers import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        await self.env.FILES.put(
            "notes/hello.txt",
            "hello from R2",
            http_metadata={"contentType": "text/plain"},
            custom_metadata={"author": "ada"},
        )

        obj = await self.env.FILES.get("notes/hello.txt")
        if obj is None:
            return Response("not found", status=404)
        return Response(
            await obj.text(),
            headers={"content-type": obj.http_metadata["contentType"]},
        )
```

`curl` returns `hello from R2` with `content-type: text/plain`.

`put` sends `http_metadata` and `custom_metadata` as written, so their keys
keep R2's camelCase names. `get` returns an
[`R2ObjectBody`](api.md#r2objectbody), or `None` when the key doesn't exist.
`list(prefix=...)` returns a dictionary whose `objects` are
[`R2Object`](api.md#r2object)s.

## Queues

```python title="src/entry.py"
from env import Env
from workers import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        await self.env.JOBS.send({"task": "resize", "id": 42}, delay_seconds=10)
        await self.env.JOBS.send_batch([{"body": {"id": 1}}, {"body": {"id": 2}}])
        return Response("queued", status=202)
```

The message body can be any value that crosses RPC, such as a `dict`, `list`,
`str` or `bytes`. `send_batch` takes one dictionary per message, with the body
under `"body"`.

## Service bindings

A service binding is a [`Fetcher`](api.md#fetcher). Its `fetch` sends an HTTP
request to the bound Worker and returns a crosswind
[`Response`](api.md#response). Any other method is an RPC call to the bound
entrypoint. This Worker binds to its own `Auth` entrypoint, so it runs without
a second project:

```jsonc title="wrangler.jsonc"
"services": [{ "binding": "AUTH", "service": "bindings-demo", "entrypoint": "Auth" }]
```

```python title="src/entry.py"
from env import Env
from workers import Request, Response, WorkerEntrypoint


class Auth(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        return Response("ok")

    async def lookup_user(self, name: str) -> dict:
        return {"name": name, "admin": False}


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        health = await self.env.AUTH.fetch("https://auth/health")
        user = await self.env.AUTH.lookup_user("ada")
        return Response.json({"health": await health.text(), "user": user})
```

```json
{"health": "ok", "user": {"name": "ada", "admin": false}}
```

The hostname in the URL doesn't matter. The request always goes to the bound
Worker. As with Durable Objects, pass RPC arguments by position.

## Keyword options and JavaScript names

Typed methods take snake_case keywords and turn them into the camelCase
options object JavaScript expects. A keyword the method doesn't know goes into
that object unchanged, so the stock SDK's spelling still works:

```python
await self.env.CACHE.put("k", "v", expiration_ttl=60)  # typed keyword
await self.env.CACHE.put("k", "v", expirationTtl=60)   # same call, stock spelling
```

A method crosswind doesn't define passes through to the JavaScript object
under its JavaScript name, with arguments and results converted as they are
for RPC. Nothing translates snake_case to camelCase for you:

```python
meta = await self.env.CACHE.getWithMetadata("k")  # passthrough, works
meta = await self.env.CACHE.get_with_metadata("k")  # typed method, also works
await self.env.CACHE.list_keys()  # neither defines it: AttributeError: list_keys
```

Bindings with no class, such as `ai` or `browser`, are a
[`Binding`](api.md#binding). Call them with their JavaScript names:

```python
# This line requires an "ai" binding named AI in wrangler.jsonc.
result = await self.env.AI.run("@cf/meta/llama-3.1-8b-instruct", {"prompt": "Hi"})
```

To reach the JavaScript object itself, read `js_object`. See
[Call JavaScript APIs](javascript.md).

## Variables and secrets

A text var or a secret is a `str`. A JSON var arrives as a Python value:

```jsonc title="wrangler.jsonc"
"vars": { "GREETING": "hello", "LIMITS": { "uploads": 10 } }
```

`self.env.LIMITS["uploads"]` is the integer `10`. Wynd types secrets from the
names in `.dev.vars`, so add each secret there for local development.
