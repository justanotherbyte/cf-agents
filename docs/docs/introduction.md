---
title: "Introduction to Crosswind"
description: "What Crosswind is, which package does what, how they fit together in one Python Worker, and which ones to install for your project."
---

Crosswind is a set of Python packages for
[Python Workers](https://developers.cloudflare.com/workers/languages/python/).
Several Cloudflare products ship their developer kits only for TypeScript, so
from Python you would call their JavaScript objects through Pyodide's foreign
function interface. Each Crosswind package ports one of those kits instead.
You subclass a Python class, override `async` methods and call typed
functions, and the package makes the JavaScript calls.

Each port keeps the defaults, timeouts and wire protocols of the TypeScript
package it copies. The Cloudflare docs for that product still describe what
your code does, and TypeScript clients such as the `useAgent` React hook
connect to a Python Worker unchanged.

## The packages

| Package                                       | Import as    | What it does                                                                         | Ports                                                                   |
| --------------------------------------------- | ------------ | ------------------------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| [`crosswind-workers`](workers/overview.md)    | `workers`    | The runtime SDK every Python Worker imports, with types on bindings, requests and Durable Objects. | [`workers-runtime-sdk`](https://github.com/cloudflare/workers-py)       |
| [`wynd`](wynd/overview.md)                    | (command)    | Builds, runs and deploys Python Workers, and drops unused package files before upload. | [pywrangler](https://github.com/cloudflare/workers-py)                  |
| [`cf-agents`](agents/overview.md)             | `agents`     | Stateful agents with WebSocket clients, schedules, durable tasks and streaming chat. | [Agents SDK](https://developers.cloudflare.com/agents/)                 |
| [`containers-py`](containers/overview.md)     | `containers` | Starts, stops and routes requests to Cloudflare Containers, and filters their outbound traffic. | [`@cloudflare/containers`](https://github.com/cloudflare/containers)   |
| [`crosswind-sandboxes`](sandboxes/overview.md) | `sandboxes` | Reads and writes files, mounts S3 buckets and backs up directories in a sandbox container. | [Sandbox SDK](https://developers.cloudflare.com/sandbox/)               |

## How they fit together

A Python Worker has two layers. The runtime SDK, imported as `workers`, gives
your code `WorkerEntrypoint`, `DurableObject`, `Request` and `Response`. A
build tool installs your dependencies for the Worker's Python version and runs
Wrangler. The other three packages sit on top of the runtime SDK: an agent, a
container and a sandbox are each a Durable Object class that you export from
your Worker.

```text
  your code:        Agent subclass   Container subclass   Durable Object + Files
                          │                  │                     │
  Crosswind SDK:      cf-agents        containers-py       crosswind-sandboxes
                          └──────────────────┼─────────────────────┘
                                             v
  runtime SDK:         workers  (crosswind-workers or workers-runtime-sdk)
                                             │
  build and deploy:          wynd  or  pywrangler  ──>  wrangler  ──>  Cloudflare
```

Each Durable Object instance has a name, its own SQLite storage and, for
containers and sandboxes, its own container. The same name always reaches the
same instance, so you can give each user, room or document its own agent or
container.

## Pick a runtime SDK and a build tool

A project ships exactly one package named `workers`. Both
`crosswind-workers` and Cloudflare's `workers-runtime-sdk` provide it, and
`crosswind-workers` refuses to import when it finds the stock SDK installed.
That choice decides your build tool, because pywrangler always installs
`workers-runtime-sdk`. `cf-agents`, `containers-py` and `crosswind-sandboxes` work with
either SDK and install neither, so your project declares the one it uses.

Wynd builds both kinds of project. It installs whichever runtime SDK your
`pyproject.toml` declares, so you can switch an existing pywrangler project to
Wynd and keep `workers-runtime-sdk`. `wynd types`, which writes a typed `Env`
class from your Wrangler config, works only with `crosswind-workers`.

For a new Worker, use `crosswind-workers` and
Wynd. `uvx wynd init` writes that project for you.

## Which package to start with

- To serve HTTP, use KV, D1, R2 or Queues, or write a Durable Object or a
  Workflow, start with [Workers](workers/getting-started.md).
- To keep state per user or per room and push it to browsers over
  WebSockets, or to stream chat replies from a model, start with
  [Agents](agents/getting-started.md).
- To run a Docker image next to your Worker, one instance per name, start
  with [Containers](containers/getting-started.md).
- To run code you don't trust, such as a script an LLM wrote, and read or
  write its files, start with [Sandboxes](sandboxes/getting-started.md).
- To shrink the upload of an existing pywrangler project, or write its config
  in Python, start with [Switch from pywrangler](wynd/getting-started.md).

## Requirements

- Python 3.12, 3.13 or 3.14 as the Worker's runtime. Your compatibility date
  and flags pick the version. See
  [Python targets](wynd/commands.md#python-targets).
- [`uv`](https://docs.astral.sh/uv/) and Node.js. Wynd and pywrangler run
  Wrangler through `npx`.
- Docker running locally, and the Workers Paid plan, for `containers-py` and
  `crosswind-sandboxes`.

## Limits

Python Workers run on [Pyodide](https://pyodide.org/en/stable/), CPython
compiled to WebAssembly. A package with C extensions works only if Pyodide
ships a build of it, so not every library on PyPI imports in a Worker. Test
your dependencies with `uvx wynd dev` before you build on them.

Each SDK's overview lists the parts of its TypeScript counterpart it doesn't
port yet:

- [Agents: Not ported yet](agents/overview.md#not-ported-yet)
- [Containers: What the SDK covers](containers/overview.md#what-the-sdk-covers)
- [Sandboxes: What the package doesn't do](sandboxes/overview.md#what-the-package-doesnt-do)
- [Workers: Status](workers/overview.md#status)

## Status

Every Crosswind package is alpha and pre-1.0. A minor release can rename or
remove an API, so pin exact versions and test each upgrade before you deploy
it:

```sh
uv add "crosswind-workers==0.3.1"
```

Crosswind is an independent community project. It isn't affiliated with or
endorsed by Cloudflare.

## Help, contributing and licensing

- Ask questions in the [Crosswind Discord](https://discord.gg/tbZTEeK35p), or
  open an issue on [GitHub](https://github.com/justanotherbyte/crosswind).
- Pull requests are welcome, including ones written with AI tools. Keep them
  to the code quality of the surrounding package.
- Each package uses the license of the TypeScript SDK it ports. The docs are
  MIT-licensed.
