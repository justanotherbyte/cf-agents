<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/banner-dark.svg">
  <img src=".github/banner-light.svg" alt="Crosswind: Python SDKs for Cloudflare Workers" width="100%">
</picture>

# Crosswind

Crosswind is a set of typed, async-first Python SDKs to help you deploy more capable applications on Cloudflare's Python Workers. It's goal is to bring Cloudflare's official TypeScript SDKs to the Python eco-system.

| Python SDK                   | Description                                                                                                                                                | Official Pair                                                   |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| [cf-agents](agents)          | Deploy Agents written in Python on Cloudflare.                                                                                                             | [Agents SDK](https://agents.cloudflare.com)                     |
| [wynd](wynd)                 | Alternative to `pywrangler`. Supports a typed `cloudflare.config.py` to configure your projects. Has minimal tree-shaking to help you reduce bundle sizes. | [workers-py](https://github.com/cloudflare/workers-py)          |
| [crosswind-workers](workers) | Typed Workers SDK for Python.                                                                                                                              | [workers-py](https://github.com/cloudflare/workers-py)          |
| [containers-py](containers)  | Python SDK to help you manage your Cloudflare Containers                                                                                                   | [Containers SDK](https://developers.cloudflare.com/containers/) |

## Licensing
Each SDK part of Crosswind uses the same licensing that its official counterpart uses. The Crosswind docs are under the MIT license.

## Contributing
I am very much open to contributions. Crosswind is a large project and open-source contributions help alot. I'm happy with AI-written PRs (I myself use AI tools - it's the only way someone can even attempt to feasibly maintain a project of this size), but do note I care about code quality. Please don't submit slop code.

If you'd like to contribute, please join the [Crosswind Discord](https://discord.gg/tbZTEeK35p).

## Getting Help

Crosswind aims to have full API and feature parity with the official SDKs (with some meaningful deviations). Often, referring (or your agents) to the Crosswind and official Cloudflare docs should do the trick. Do open issues if you must though!

## Limitations

The official Cloudflare Python workers runtime is inherently limited. Not all Python libraries work with [Pyodide](https://pyodide.org/en/stable/), which powers Python Workers.
