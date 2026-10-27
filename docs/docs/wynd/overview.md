---
title: "Wynd: build and deploy Python Workers"
description: "What Wynd does, how it differs from pywrangler, what its pruning saves, and when to use it."
---

Wynd is a command-line tool that builds, runs and deploys
[Python Workers](https://developers.cloudflare.com/workers/languages/python/).
It replaces pywrangler and keeps pywrangler's project layout: a
`pyproject.toml`, a Wrangler config, your code in `src/`, and packages in
`python_modules/`. You run it with `uvx wynd`, and it runs Wrangler 4.129.0
for you. It can also read the config from a Python file,
[`cloudflare.config.py`](python-config.md).

```sh
uvx wynd init     # write a new crosswind-workers project
uvx wynd dev      # install packages for the Worker's Python, then wrangler dev
uvx wynd deploy   # prune unused packages, then wrangler deploy
uvx wynd tail     # anything else goes to Wrangler unchanged
```

## What it does

For `wynd dev`, Wynd reads your Wrangler config, or compiles
`cloudflare.config.py` into one, picks the Python version the
Worker will run on, installs your dependencies for that version into
`python_modules/`, and starts Wrangler's dev server.

For `wynd deploy`, `wynd versions upload` and `wynd preview`, Wynd also:

1. Follows the imports from your entry module through every installed
   package.
2. Copies your source and the package files those imports can reach into
   `.wynd/build/`. It leaves out tests, docs, type stubs and modules nothing
   imports, and strips comments and docstrings from the package modules it
   keeps.
3. Runs Wrangler on that copy.

Every other command, such as `wynd login`, `wynd tail` or
`wynd kv namespace create`, goes straight to Wrangler.

## How it differs from pywrangler

| Behaviour                                         | pywrangler                                       | Wynd                                                       |
| ------------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------- |
| Unused vendored files                             | Uploaded                                         | Dropped before upload                                      |
| Python version with `--env` or `--config`         | Read from the top level of the default config    | Read from the environment and config you pass              |
| Reinstall after a compatibility date change       | Only with `sync --force`                         | Automatic                                                  |
| Runtime SDK                                       | Always adds `workers-runtime-sdk`                | Installs the one you declare, `crosswind-workers` or `workers-runtime-sdk` |
| Typed bindings                                    | TypeScript types converted to Python             | `wynd types` writes a Python `Env` class for crosswind-workers |
| Editor environment                                | A separate `.venv-workers`                       | Your normal `.venv` from `uv sync`                         |
| New projects                                      | `pywrangler init`                                | `wynd init`, with a `cloudflare.config.py`                 |
| Config file                                       | Wrangler's                                       | Wrangler's, or `cloudflare.config.py`                      |

## What pruning saves

> **Warning**
>
> The pruner is in early alpha, and its rules will change often between
> releases. [Prune the upload](pruning.md) says what it handles well today
> and what it doesn't do.

Pruning drops Python files that nothing imports, plus tests, docs and type
stubs. It strips comments and docstrings from the package modules it keeps,
and keeps compiled extensions and data files whole. How much it saves depends
on your packages. These figures come from Wynd 0.3.0 with a Python 3.14
target:

| Worker                                   | `python_modules/` | After pruning | Saved |
| ---------------------------------------- | ----------------: | ------------: | ----: |
| `wynd init`'s project, crosswind-workers 0.2.0 only | 130,629 bytes | 84,128 bytes | 36% |
| workers-runtime-sdk only                 | 113,809 bytes     | 62,897 bytes  | 45%   |
| FastAPI and pydantic on workers-runtime-sdk | 9,376,122 bytes   | 7,872,724 bytes | 16% |

In the FastAPI Worker, pydantic-core's compiled extension is 4,689,616 of the
bytes after pruning, and Wynd keeps it whole. `wynd deploy --dry-run` prints
the numbers for your Worker. See [Prune the upload](pruning.md).

## When to use it

Use Wynd for any Python Worker built with
[crosswind-workers](../workers/overview.md), which doesn't run under pywrangler.

Start a new project with `uvx wynd init`, which writes a crosswind-workers
Worker configured in [`cloudflare.config.py`](python-config.md).

On the stock SDK, Wynd is worth switching to if you deploy with `--env` or
more than one config, if you change compatibility dates, or if you want to see
what each upload contains. [Switch from pywrangler](getting-started.md) moves
an existing project.

## Requirements

- [uv](https://docs.astral.sh/uv/) 0.12.3 or later on `PATH`.
- Node.js on `PATH`, for `npx`.
- Linux or macOS. Windows is untested.

## Status

Wynd is pre-1.0, and its commands and options may change between minor
releases. It has no `--upgrade` flag for `sync`, and `wynd dev` always runs
the full `python_modules/`, never the pruned tree. Wynd is an
independent community project, not an official Cloudflare tool.

## Next steps

- [Switch from pywrangler](getting-started.md) moves an existing project to
  Wynd.
- [Write config in Python](python-config.md) creates a project with
  `wynd init` and configures it in `cloudflare.config.py`.
- [Commands and configuration](commands.md) lists every command, flag and
  setting.
