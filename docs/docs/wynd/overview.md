---
title: "Wynd: build and deploy Python Workers"
description: "What Wynd does, how it differs from pywrangler, what its pruning saves, and when to use it."
---

Wynd is a command-line tool that builds, runs and deploys
[Python Workers](https://developers.cloudflare.com/workers/languages/python/).
It replaces pywrangler and keeps pywrangler's project layout: a
`pyproject.toml`, a Wrangler config, your code in `src/`, and packages in
`python_modules/`. You run it with `uvx wynd`, and it runs a pinned Wrangler
for you.

```sh
uvx wynd dev      # install packages for the Worker's Python, then wrangler dev
uvx wynd deploy   # prune unused packages, then wrangler deploy
uvx wynd tail     # anything else goes to Wrangler unchanged
```

## What it does

For `wynd dev`, Wynd reads your Wrangler config, picks the Python version the
Worker will run on, installs your dependencies for that version into
`python_modules/`, and starts Wrangler's dev server.

For `wynd deploy`, `wynd versions upload` and `wynd preview`, Wynd also:

1. Follows the imports from your entry module through every installed
   package.
2. Copies your source and the files those imports can reach into
   `.wynd/build/`, and leaves out tests, docs, type stubs and modules nothing
   imports.
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

## What pruning saves

Pruning drops Python files that nothing imports, plus tests, docs and type
stubs. It copies every file it keeps byte for byte, and keeps compiled
extensions and data files whole. How much it saves depends on your packages:

| Worker                                   | `python_modules/` | After pruning | Saved |
| ---------------------------------------- | ----------------: | ------------: | ----: |
| A Durable Object counter, crosswind-workers only | 127,013 bytes | 92,668 bytes | 27% |
| FastAPI and pydantic                     | 9,627,191 bytes   | 9,417,629 bytes | 2.2% |
| An agents app with pydantic and Jinja2   | 7,887,580 bytes   | 7,654,619 bytes | 3.0% |

Most of a FastAPI Worker's bytes are pydantic-core's compiled extension,
which Wynd keeps whole. `wynd deploy --dry-run` prints the numbers for your
Worker. See [Prune the upload](pruning.md).

## When to use it

Use Wynd for any Python Worker built with
[crosswind-workers](../workers/overview.md), which doesn't run under pywrangler.

On the stock SDK, Wynd is worth switching to if you deploy with `--env` or
more than one config, if you change compatibility dates, or if you want to see
what each upload contains. It doesn't create projects. Start a new one by hand
or from `pywrangler init`, then [switch it to Wynd](getting-started.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) 0.12.3 or later on `PATH`.
- Node.js on `PATH`, for `npx`.
- Linux or macOS. Windows is untested.

## Status

Wynd is pre-1.0, and its commands and options may change between minor
releases. It doesn't yet have `init`, an `--upgrade` flag for `sync`, or a way
to run the pruned tree under `dev`. Wynd is an
independent community project, not an official Cloudflare tool.

## Next steps

- [Switch from pywrangler](getting-started.md) moves an existing project to
  Wynd.
- [Commands and configuration](commands.md) lists every command, flag and
  setting.
