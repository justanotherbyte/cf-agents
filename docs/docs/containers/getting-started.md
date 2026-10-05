---
title: Getting started
---

# Getting started

`containers-py` lets a [Python Worker](https://developers.cloudflare.com/workers/languages/python/)
start, stop and route requests to
[Cloudflare Containers](https://developers.cloudflare.com/containers/). It is a
Python port of the
[`@cloudflare/containers`](https://developers.cloudflare.com/containers/api/container-class/)
TypeScript package (v0.3.7), so the concepts, defaults and behaviour match. The
API has been reworked to read like Python.

```python
from containers import Container, get_container
from workers import Request, Response, WorkerEntrypoint


class MyContainer(Container):
    default_port = 8080  # Port the container is listening on
    sleep_after = "10m"  # Stop the instance if requests not sent for 10 minutes


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        # Get the container instance for the given session ID
        session_id = request.headers.get("x-session-id", "default")
        container = get_container(self.env.MY_CONTAINER, session_id)
        # Pass the request to the container instance on its default port
        return await container.fetch(request)
```

!!! note "Status"

    `containers-py` is pre-1.0 and its API may change between minor releases.
    It is an independent community project, not an official Cloudflare SDK.

## How it works

A container instance is always managed by a
[Durable Object](https://developers.cloudflare.com/durable-objects/). The
`Container` class _is_ that Durable Object. Subclass it, configure it with
class attributes, and the SDK handles starting the container, waiting for its
ports, proxying requests to it, and putting it to sleep when it goes idle.

```
client ──▶ Worker (Default) ──▶ Durable Object (MyContainer) ──▶ container
```

Each Durable Object ID maps to one container instance. Which ID you ask for
decides which container serves a request. See
[Routing and scaling](routing.md) for the routing patterns, and
[Lifecycle of a Container](https://developers.cloudflare.com/containers/concepts/architecture/)
for what the platform does underneath.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) and Node.js, to build and deploy
  [Python Workers](https://developers.cloudflare.com/workers/languages/python/).
- A Docker-compatible CLI and engine running locally (`docker info` should
  succeed). Wrangler builds your image with it in both `dev` and `deploy`. See
  [Local development](https://developers.cloudflare.com/containers/guides/local-dev/).
- A Cloudflare account on the Workers Paid plan, which Containers requires. See
  [Pricing](https://developers.cloudflare.com/containers/platform/pricing/).

## 1. Create a Python Worker

Create a project with `pywrangler`, then add `containers-py`:

```sh
uvx --from workers-py pywrangler init
cd hello-containers  # the directory you chose during init
uv add containers-py
```

Your `pyproject.toml` should end up with something like:

```toml title="pyproject.toml"
[project]
name = "hello-containers"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["containers-py"]

[dependency-groups]
dev = ["workers-py"]
```

The package is installed as `containers-py` and imported as `containers`. It
depends on [`workers-runtime-sdk`](https://pypi.org/project/workers-runtime-sdk/),
which provides `workers.DurableObject`, `Request` and `Response`. For how
dependencies get bundled into your Worker, see
[Packages](https://developers.cloudflare.com/workers/languages/python/packages).

## 2. Write the container

Any image that listens on a port works. This one is a small
[FastAPI](https://fastapi.tiangolo.com/) app served by
[uvicorn](https://www.uvicorn.org/) on port `8080`, with its dependencies
managed by [`uv`](https://docs.astral.sh/uv/). The container is a separate
Python project from the Worker, so it gets its own `pyproject.toml`:

```toml title="container_src/pyproject.toml"
[project]
name = "container-app"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "pydantic-settings>=2.6",
    "uvicorn[standard]>=0.32",
]
```

Lock it once, so every image build installs the same versions:

```sh
cd container_src && uv lock && cd ..
```

```python title="container_src/main.py"
from fastapi import FastAPI
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Read from the MESSAGE environment variable
    message: str = "no message set"


class Echo(BaseModel):
    text: str


settings = Settings()
app = FastAPI(title="hello-containers")


@app.get("/")
async def index() -> dict[str, str]:
    return {"message": settings.message}


@app.post("/echo")
async def echo(body: Echo) -> Echo:
    return body
```

The Dockerfile copies `uv` in from its official image and installs the locked
dependencies before it copies your code, so editing `main.py` doesn't reinstall
them:

```dockerfile title="Dockerfile"
FROM python:3.13-slim

# Pin a specific uv version in production
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first: this layer is cached until the lockfile changes
COPY container_src/pyproject.toml container_src/uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY container_src/main.py ./

# Run from the virtual environment uv created
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Wrangler uses your project root as the build context, so keep your Worker's
virtual environment and vendored packages out of it:

```text title=".dockerignore"
.git
.venv
.wrangler
python_modules
node_modules
**/__pycache__
```

uvicorn shuts down cleanly on `SIGTERM`, which is what the SDK sends when a
container goes to sleep.

!!! tip

    Your server doesn't need a health-check route. While the container starts,
    the SDK polls the port it's waiting for with plain HTTP requests (to
    `http://ping` by default), and any HTTP response counts as ready, including
    a `404`. Change the host and path
    with [`ping_endpoint`](container-class.md#properties).

## 3. Define the Container class

Replace `src/entry.py`:

```python title="src/entry.py"
from urllib.parse import parse_qs, urlparse

from containers import Container, get_container, get_random
from workers import Request, Response, WorkerEntrypoint


class MyContainer(Container):
    # Port the container is listening on
    default_port = 8080
    # Stop the instance if requests are not sent for 2 minutes
    sleep_after = "2m"
    # Environment variables passed to the container on every start
    env_vars = {"MESSAGE": "I was passed in via the Container class!"}

    async def on_start(self) -> None:
        print("Container successfully started")

    async def on_stop(self, *, exit_code: int, reason: str) -> None:
        print(f"Container stopped with exit code {exit_code} ({reason})")

    async def on_error(self, error: Exception) -> None:
        print("Container error:", error)
        raise error


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        query = parse_qs(urlparse(request.url).query)

        if "id" in query:
            # One container per ID: ?id=<id>
            container = get_container(self.env.MY_CONTAINER, query["id"][0])
        elif "lb" in query:
            # Spread requests across 3 interchangeable instances: ?lb
            container = await get_random(self.env.MY_CONTAINER, 3)
        else:
            # A single shared instance
            container = get_container(self.env.MY_CONTAINER)

        # The whole request goes to the FastAPI app, query string included
        return await container.fetch(request)
```

`MyContainer` has to be importable from the entry module (`src/entry.py`),
because that's where the runtime looks for the Durable Object class named in
your Wrangler config.

## 4. Configure Wrangler

```jsonc title="wrangler.jsonc"
{
  "name": "hello-containers",
  "main": "src/entry.py",
  "compatibility_date": "2026-05-12",
  "compatibility_flags": ["python_workers"],
  "containers": [
    {
      "class_name": "MyContainer",
      "image": "./Dockerfile",
      "max_instances": 10
    }
  ],
  "durable_objects": {
    "bindings": [{ "class_name": "MyContainer", "name": "MY_CONTAINER" }]
  },
  "migrations": [{ "tag": "v1", "new_sqlite_classes": ["MyContainer"] }]
}
```

- **`containers[].class_name`** links the image to your `Container` subclass. It
  must match the class name in `src/entry.py`.
- **`containers[].image`** is a path to a Dockerfile, a directory, or an image
  in a registry. See
  [Image management](https://developers.cloudflare.com/containers/guides/image-management/).
- **`containers[].max_instances`** caps how many instances of this class can
  run at once.
- **`durable_objects.bindings`** exposes the class to your Worker as
  `self.env.MY_CONTAINER`.
- **`migrations`** declares the class as a
  [SQLite-backed](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/)
  Durable Object. `Container` stores its state and schedules in SQLite, so it
  won't work on the legacy key-value storage backend.

For every option, including `instance_type` and rollout settings, see
[Wrangler configuration](https://developers.cloudflare.com/containers/configuration/wrangler/).

!!! warning

    If the `containers` entry is missing or its `class_name` doesn't match,
    the `Container` constructor raises `RuntimeError: Containers have not been
    enabled for this Durable Object class`.

## 5. Run locally

```sh
uv run pywrangler dev
```

Wrangler builds the image with Docker and runs containers on your machine. The
first build downloads the base image and installs your dependencies, so it
takes a while. Later builds reuse the cached layers. Worker code reloads on
save, but container code doesn't: press `r` in the `dev` session to rebuild the
image.

```sh
curl "http://localhost:8787/?id=abc"
# {"message":"I was passed in via the Container class!"}

curl -X POST "http://localhost:8787/echo?id=abc" \
  -H "content-type: application/json" \
  -d '{"text": "hello"}'
# {"text":"hello"}
```

The first request to a new ID cold-starts a container, which usually takes a
few seconds. Requests with the same `id` reach the same container. Requests after that go to the running instance until it sleeps.

## 6. Deploy

```sh
uv run pywrangler deploy
```

Wrangler builds the image, pushes it to Cloudflare's registry and deploys the
Worker. The first deploy takes a few minutes to provision. Until it finishes,
requests that need a container get a `503`: _"There is no Container instance
available at this time"_. Check progress with:

```sh
npx wrangler containers list
```

or in the [Containers dashboard](https://dash.cloudflare.com/?to=/:account/workers/containers).
See [Deploy Containers](https://developers.cloudflare.com/containers/guides/deploy/)
for more on rollouts.

## Next steps

- [Lifecycle](lifecycle.md): states, hooks, sleeping and stopping.
- [Routing and scaling](routing.md): pick which instance serves a request, and
  call container methods from your Worker.
- [Outbound traffic](outbound-traffic.md): control and intercept what the
  container can reach.
- [Examples](examples.md): copy-paste patterns.
- [Container class](container-class.md): the full API reference.
- [Coming from TypeScript](coming-from-typescript.md): how the Python API maps
  to `@cloudflare/containers`.
