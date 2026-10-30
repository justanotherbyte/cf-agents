<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/banner-dark.svg">
  <img src=".github/banner-light.svg" alt="Crosswind: Python SDKs for Cloudflare Workers" width="100%">
</picture>

# Crosswind

[![Crosswind Community](https://discord.com/api/guilds/1564745929733443664/embed.png)](https://discord.gg/tbZTEeK35p)

Crosswind provides Python packages for Cloudflare's [Python Workers](https://developers.cloudflare.com/workers/languages/python/). Each package ports a Cloudflare SDK that otherwise ships only for TypeScript. The Python APIs are typed and `async`.

Start with the [Crosswind documentation](https://crosswind.viswa.space) to choose a package.

| Package                          | What it does                                                                                     | Ports                                                           |
| -------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- |
| [cf-agents](agents)              | Stateful agents with WebSocket clients, schedules, durable tasks and streaming chat.             | [Agents SDK](https://agents.cloudflare.com)                     |
| [wynd](wynd)                     | Replaces `pywrangler`. Reads a typed `cloudflare.config.py` and drops unused files before upload. | [workers-py](https://github.com/cloudflare/workers-py)          |
| [crosswind-workers](workers)     | The `workers` runtime SDK, with type hints on bindings, requests and Durable Objects.            | [workers-py](https://github.com/cloudflare/workers-py)          |
| [containers-py](containers)      | Starts, stops and routes requests to Cloudflare Containers.                                      | [Containers SDK](https://developers.cloudflare.com/containers/) |
| [crosswind-sandboxes](sandboxes) | File operations, S3 mounts and directory backups in a running container.                         | [Sandbox SDK](https://developers.cloudflare.com/sandbox/)       |

## Status

Every package is alpha and pre-1.0. Pin exact versions because a minor release can rename or remove an API.

Python Workers run on [Pyodide](https://pyodide.org/en/stable/), so a library with C extensions imports only if Pyodide ships a build of it.

## Getting help

Each package aims to match the API and behaviour of the SDK it ports, with a few deliberate differences. Check the [Crosswind docs](https://crosswind.viswa.space) and the Cloudflare docs for the product first. Ask other questions in [Discord](https://discord.gg/tbZTEeK35p) or open an issue.

## Contributing

Contributions are welcome, including AI-written pull requests. Keep them to the code quality of the surrounding package.

## Licensing

Each package uses the license of the official SDK it ports. The docs are MIT-licensed.
