---
title: "Switch a Python Worker from pywrangler to Wynd"
description: "Move a pywrangler project to Wynd: declare the runtime SDK, run it locally, check the pruned upload and deploy."
---

This guide moves an existing pywrangler project to Wynd without changing its
code. By the end, `uvx wynd dev` serves the Worker and `uvx wynd deploy`
uploads it. To start a new project instead, run `uvx wynd init`, which
[writes a crosswind-workers project](python-config.md#create-a-project), or
follow [Build a typed Python Worker](../workers/getting-started.md).

## Prerequisites

- A Python Worker that runs under `pywrangler dev`.
- [uv](https://docs.astral.sh/uv/) 0.13.4 or later. Check with `uv --version`,
  and update with `uv self update`.
- Node.js, which provides `npx`.

## 1. Declare the runtime SDK

pywrangler adds `workers-runtime-sdk` for you. Wynd installs only the SDK your
project lists, so add it to `[project] dependencies`:

```sh
uv add workers-runtime-sdk
```

To move to crosswind-workers at the same time, run
`uv add crosswind-workers` instead. See
[Migrate from workers-runtime-sdk](../workers/migrate.md).

Without either one, every Wynd command that installs packages stops with:

```text
wynd: error: pyproject.toml declares neither crosswind-workers nor workers-runtime-sdk. Add one to [project] dependencies.
```

## 2. Remove pywrangler

```sh
uv remove --dev workers-py
rm -rf .venv-workers
```

Wynd doesn't use `.venv-workers`. Your editor uses the normal `.venv`, which
`uv sync` keeps up to date.

## 3. Check where `main` points

Open `wrangler.jsonc`. `main` must name a file inside a subdirectory, such as
`src/entry.py`, which is pywrangler's default. Wynd copies that directory
when it deploys and fails if the entry file sits next to `wrangler.jsonc`.

## 4. Run it locally

```sh
uvx wynd dev
```

Wynd installs your packages for the Worker's Python version into
`python_modules/`, writes `pylock.toml`, and starts `wrangler dev` 4.129.0:

```text
 ⛅️ wrangler 4.129.0
...
[wrangler:info] Ready on http://localhost:8787
```

Send the requests you normally test with. They behave as they did under
pywrangler.

The next `wynd dev` skips the install unless `pyproject.toml`,
`pylock.toml`, the compatibility date, the compatibility flags or
[another input](commands.md#when-packages-reinstall) changed.

## 5. Check the upload

```sh
uvx wynd deploy --dry-run
```

Wynd prints the size of `python_modules/` before and after pruning, then
Wrangler's dry-run output. For a Worker whose only dependency is
`workers-runtime-sdk`, on a 2026-09-08 compatibility date:

```text
wynd: python_modules is 113,809 bytes, 62,897 after pruning (50,912 fewer). Each file's decision is in .wynd/shake.txt.
...
Total Upload: 61.79 KiB / gzip: 15.50 KiB
```

`.wynd/shake.txt` lists every file with `kept` or `dropped` and the reason. If
your Worker imports a module by a name built at runtime, Wynd can't see that
import and drops the module. [Prune the upload](pruning.md#keep-modules-wynd-cant-see)
shows how to keep it.

## 6. Deploy

```sh
uvx wynd deploy
```

Wynd uploads the pruned copy in `.wynd/build/`, and Wrangler prints the
Worker's URL.

## 7. Update `.gitignore` and scripts

Add Wynd's working directory:

```text title=".gitignore"
python_modules/
.wynd/
```

Commit `pylock.toml`, so every machine installs the same versions. Then
replace `uv run pywrangler` with `uvx wynd` in scripts and CI:

| pywrangler                       | Wynd                       |
| -------------------------------- | -------------------------- |
| `uv run pywrangler dev`          | `uvx wynd dev`             |
| `uv run pywrangler deploy`       | `uvx wynd deploy`          |
| `uv run pywrangler sync`         | `uvx wynd sync`            |
| `uv run pywrangler types`        | `uvx wynd types`, crosswind-workers only |
| `npx wrangler <command>`         | `uvx wynd <command>`       |

## Next steps

- [Commands and configuration](commands.md) lists every command and setting.
- [Migrate from workers-runtime-sdk](../workers/migrate.md) adds typed
  bindings.
- [Write config in Python](python-config.md#move-a-wrangler-config-to-python)
  replaces `wrangler.jsonc` with a `cloudflare.config.py`.
