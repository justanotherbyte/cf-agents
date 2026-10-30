---
title: "Serve a FastAPI or Flask app from a Python Worker"
description: "Run an ASGI app such as FastAPI or a WSGI app such as Flask as a Python Worker with crosswind-workers' asgi and wsgi adapters, and read bindings from routes."
---

Serve an existing FastAPI or Flask app from a Python Worker with a
crosswind-workers adapter. `workers.asgi` serves ASGI apps such as FastAPI and
Starlette. For a new app, use FastAPI with this adapter. It runs `async`
routes, supports WebSockets and keeps background work alive through `ctx`.

`workers.wsgi` serves WSGI apps such as Flask. Use it for an existing Flask
app. WSGI runs synchronously, so a slow route blocks the Worker. The adapter
has no WebSocket support.

## FastAPI

Add FastAPI next to the SDK:

```toml title="pyproject.toml"
[project]
name = "fastapi-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["crosswind-workers", "fastapi"]
```

=== "wrangler.jsonc"

    ```jsonc
    {
      "name": "fastapi-worker",
      "main": "src/entry.py",
      "compatibility_date": "2026-09-08",
      "compatibility_flags": ["python_workers"],
      "vars": { "GREETING": "hello" }
    }
    ```

=== "wrangler.toml"

    ```toml
    name = "fastapi-worker"
    main = "src/entry.py"
    compatibility_date = "2026-09-08"
    compatibility_flags = ["python_workers"]

    [vars]
    GREETING = "hello"
    ```

=== "cloudflare.config.py"

    ```python
    from wynd.config import Config, Worker, bindings

    config = Config(
        worker=Worker(
            name="fastapi-worker",
            entrypoint="src/entry.py",
            compatibility_date="2026-09-08",
            env={"GREETING": bindings.Text("hello")},
        ),
    )
    ```

```python title="src/entry.py"
from fastapi import FastAPI
from pydantic import BaseModel

from workers import WorkerEntrypoint, asgi

app = FastAPI()


class Item(BaseModel):
    name: str


@app.get("/")
async def root(env=asgi.env):
    return {"greeting": env.GREETING}


@app.post("/items")
async def create_item(item: Item):
    return {"name": item.name.upper()}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env, self.ctx)
```

Run it with `uvx wynd dev`, then:

```sh
curl http://localhost:8787/
# {"greeting":"hello"}
curl -X POST http://localhost:8787/items -H 'content-type: application/json' -d '{"name": "widget"}'
# {"name":"WIDGET"}
curl -X POST http://localhost:8787/items -H 'content-type: application/json' -d '{}'
# {"detail":[{"type":"missing","loc":["body","name"],"msg":"Field required","input":{}}]}
```

Pass `self.ctx` to `asgi.fetch`. The adapter uses it to keep the request
alive while your app finishes work after the response starts, such as a
streaming body or a `BackgroundTask`. Without `ctx`, it falls back to the
module-level `wait_until`. That function doesn't keep the Worker alive under
`wynd dev`, so that work can stop partway.

The adapter differs from a server such as Uvicorn in three ways:

- It runs the app's lifespan startup before every request and the shutdown
  after it, because a Worker has no long-lived process. Keep startup work
  short.
- It reads the whole request body before your route runs.
- The ASGI scope has no `client`, `server` or `root_path` keys, so
  `request.client` is `None` in FastAPI.

### Read bindings in a route

`asgi.env` is a FastAPI dependency that returns the Worker's `env`, as in the
`root` route above. Outside FastAPI, read it from the ASGI scope as
`request.scope["env"]`.

### Sync routes and threads

A Worker has one thread. FastAPI runs sync routes, sync dependencies and
`StaticFiles` through `anyio.to_thread`, which would fail with
`RuntimeError: can't start new thread`. crosswind-workers patches
`anyio.to_thread.run_sync` to call the function inline, so a `def` route works.
It blocks the Worker while it runs, so prefer `async def` for anything that
waits on I/O.

### WebSockets

When a request carries `Upgrade: websocket`, call `asgi.websocket` in place
of `asgi.fetch`. `asgi.entrypoint(app)` builds a `WorkerEntrypoint` that
checks the header for you:

```python
Default = asgi.entrypoint(app)
```

## Flask

```toml title="pyproject.toml"
[project]
name = "flask-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["crosswind-workers", "flask"]
```

```python title="src/entry.py"
from flask import Flask, request

from workers import WorkerEntrypoint, wsgi

app = Flask(__name__)


@app.get("/")
def root():
    env = request.environ["workers.env"]
    return {"greeting": env.GREETING}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await wsgi.fetch(app, request, self.env)
```

Use the same `wrangler.jsonc` with its name changed to `flask-worker`. Then
`curl http://localhost:8787/` returns `{"greeting":"hello"}`.

The WSGI environ holds the Worker's env under `workers.env`. `wsgi.entrypoint(app)`
returns the same `Default` class.

On Python 3.13 and later, `request.get_data()` and other reads of
`wsgi.input` stream the body as Flask reads it. Python 3.12 lacks the
`run_sync` call that streaming needs, so there the adapter reads the whole
body into memory before Flask runs.

## Compressed responses

The adapters leave a body alone when your app sets `Content-Encoding`, so
`GZipMiddleware` and similar middleware send one layer of compression, not
two.
