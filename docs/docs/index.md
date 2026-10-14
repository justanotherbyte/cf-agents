---
template: home.html
title: Python SDKs for Cloudflare Workers
hide:
  - navigation
  - toc
---

=== "Agents"

    ```python
    from workers import Request, Response, WorkerEntrypoint
    from agents import Agent, callable, route_agent_request


    class Counter(Agent):
        initial_state = {"count": 0}

        @callable  # clients call this over a WebSocket
        async def increment(self, by: int = 1) -> int:
            self.set_state({"count": self.state["count"] + by})  # saved, sent to every client
            return self.state["count"]


    class Default(WorkerEntrypoint):
        async def fetch(self, request: Request) -> Response:
            # /agents/counter/<name> reaches the counter called <name>
            return await route_agent_request(request, self.env) or Response("Not found", status=404)
    ```

=== "Containers"

    ```python
    from workers import Request, Response, WorkerEntrypoint
    from containers import Container, get_container


    class Transcoder(Container):
        default_port = 8080
        sleep_after = "5m"
        enable_internet = False


    class Default(WorkerEntrypoint):
        async def fetch(self, request: Request) -> Response:
            # One container per video, started on the first request
            video_id = request.url.rsplit("/", 1)[-1]
            container = get_container(self.env.TRANSCODER, video_id)
            return await container.fetch(request)
    ```

## What Crosswind is

[Python Workers](https://developers.cloudflare.com/workers/languages/python/)
run Python on Cloudflare's network. Several Cloudflare products ship their
developer kits only for TypeScript, so using them from Python means calling
JavaScript objects through Pyodide's foreign function interface.

Each Crosswind package ports one of those kits to Python. You subclass a Python
class, override `async` methods and call typed functions, and the package
makes the JavaScript calls.

## Packages

| Project                               | Package             | What it does                                                                                 | Version                                                               |
| ------------------------------------- | ------------------- | -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| [Agents](agents/overview.md)          | `cf-agents`         | Stateful agents with WebSocket clients, schedules, durable tasks and streaming chat.         | ![cf-agents on PyPI](https://img.shields.io/pypi/v/cf-agents)         |
| [Containers](containers/overview.md)  | `containers-py`     | Start, stop and route requests to Cloudflare Containers, and filter their outbound traffic.  | ![containers-py on PyPI](https://img.shields.io/pypi/v/containers-py) |
| [Workers](workers/overview.md)        | `crosswind-workers` | Drop-in replacement for the official `workers` package, with typed, idiomatic APIs.          | ![crosswind-workers on PyPI](https://img.shields.io/pypi/v/crosswind-workers) |
| [Wynd](wynd/overview.md)              | `wynd`              | Replaces pywrangler. It drops vendored files your Worker never imports before each upload.   | ![wynd on PyPI](https://img.shields.io/pypi/v/wynd)                   |

## Why Crosswind

<div class="grid cards" markdown>

-   **Pythonic, all the way**

    Typed, `async`-first APIs. Crosswind opts for Pythonic APIs rather than literal translations of TypeScript code.

-   **Same behaviour as TypeScript**

    Each SDK keeps the defaults and behaviour of the TypeScript SDK it ports, so the TypeScript docs still describe what your code does.

-   **Python framework integrations**

    Planned: integrations with FastAPI, Starlette, Django and other Python web frameworks. None exist yet.

-   **Works with the TypeScript ecosystem**

    Each SDK speaks the same protocols as its TypeScript counterpart, so existing TypeScript clients and frontend libraries connect without changes where possible.

</div>

## Demo

<div class="cw-demo">
  <button class="md-button md-button--primary" type="button">Load the demo</button>
</div>

<script>
  // The game focuses its input when it loads, which scrolls this page, so load it on click
  document.querySelector(".cw-demo button").addEventListener("click", (event) => {
    const frame = document.createElement("iframe");
    frame.src = "https://agents-py-mosslight-valley.reachvishm8605.workers.dev/";
    frame.title = "Mosslight Valley demo game";
    frame.allow = "fullscreen; autoplay; gamepad";
    event.currentTarget.parentElement.replaceChildren(frame);
  });
</script>

Mosslight Valley is a small game that runs on Python Workers. Its server is
written with `cf-agents`.
[Open it in a new tab](https://agents-py-mosslight-valley.reachvishm8605.workers.dev/){ target="_blank" }

## Status

The published packages are pre-1.0, and their APIs may change between minor
releases.
Each SDK's overview lists the features it doesn't port yet. Crosswind is an
independent community project. It isn't affiliated with or endorsed by
Cloudflare.
