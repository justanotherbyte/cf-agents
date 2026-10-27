---
title: "Wynd commands and configuration reference"
description: "Every Wynd command including init, the flags Wynd reads, [tool.wynd] settings, Python target selection, when packages reinstall, the staged config and the files Wynd writes."
---

Run Wynd with `uvx wynd <command>`. To pin its version, run
`uvx wynd@0.3.0 <command>`. Wynd needs uv 0.12.3 or later and Node.js on
`PATH`, and runs Wrangler 4.129.0 through `npx --yes wrangler@4.129.0`.

## Commands

| Command                        | What Wynd does                                                                                                   |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| `wynd init [directory]`        | Writes a new crosswind-workers project into `directory`, or the current directory, with a [`cloudflare.config.py`](python-config.md#create-a-project). Refuses a directory that already has a project. Doesn't run Wrangler. |
| `wynd sync`                    | Installs packages for the Worker's Python target into `python_modules/`, if [anything changed](#when-packages-reinstall). Doesn't run Wrangler. |
| `wynd types [--check]`         | Writes `env.py`, or with `--check` exits `1` if it's out of date. crosswind-workers only. See [Generate typed bindings](types.md). |
| `wynd dev [args]`              | Syncs, regenerates `env.py` on crosswind-workers projects, then runs `wrangler dev [args]` on the full `python_modules/`. |
| `wynd deploy [args]`           | Syncs, regenerates `env.py`, [prunes](pruning.md) into `.wynd/build/`, then runs `wrangler deploy [args]` on the staged config. |
| `wynd versions upload [args]`  | As `deploy`, with `wrangler versions upload`.                                                                    |
| `wynd preview [args]`          | As `deploy`, with `wrangler preview`. `preview delete`, `preview settings`, `preview secret` and `preview base-config` pass through. |
| Any other command              | Runs `wrangler <command> [args]` without syncing. Unchanged for a Wrangler config. For a `cloudflare.config.py`, Wynd [compiles it first](python-config.md#check-the-compiled-config) and passes the compiled file. |

With `--dry-run`, the three upload commands print one line before Wrangler's
output:

```text
wynd: python_modules is 127,013 bytes, 92,668 after pruning (34,345 fewer). Each file's decision is in .wynd/shake.txt.
```

### Exit codes

| Code         | Meaning                                                              |
| ------------ | -------------------------------------------------------------------- |
| Wrangler's   | Wynd ran Wrangler, and it exited with this code.                     |
| `1`          | Wynd stopped before Wrangler ran. The error starts with `wynd: error:`, or is `wynd: npx not found on PATH`. An exception raised by your `cloudflare.config.py` prints a Python traceback. |
| `128 + N`    | Wrangler was killed by signal `N`.                                   |

## Flags Wynd reads

Wynd reads these flags to choose the config and the target, and passes them
on to Wrangler unchanged:

| Flag                     | Effect                                                                                  |
| ------------------------ | --------------------------------------------------------------------------------------- |
| `-c`, `--config <path>`  | Uses this config, a Wrangler file or a `cloudflare.config.py`, in place of the [discovered one](#config-discovery). |
| `-e`, `--env <name>`     | Uses this [environment](#environments) from a Wrangler config, or passes this [mode](python-config.md#use-modes) to `cloudflare.config.py`. Defaults to `$CLOUDFLARE_ENV`. |
| `--cwd <path>`           | Looks for `pyproject.toml` and the config from this directory.                          |

For the upload commands, Wynd replaces `--config` with the path of the staged
config in `.wynd/build/` and keeps every other argument in its order. For a
`cloudflare.config.py`, Wynd also removes `-e` and `--env`, and runs Wrangler
without `CLOUDFLARE_ENV`, because the compiled file has no environments.

`dev`, `deploy`, `versions upload` and `preview` refuse
`--compatibility-date`, `--compatibility-flags` and `--latest`, and exit with
`1`. Those flags would change the Python version after Wynd installed packages
for the config's version. Set the date and flags in the config instead.

## `[tool.wynd]`

Settings in `pyproject.toml`:

```toml title="pyproject.toml"
[tool.wynd]
keep = ["myplugins.csv_export"]
allow-build = false
keep-docstrings = false
```

| Key           | Type        | Default | Description                                                                                       |
| ------------- | ----------- | ------- | ------------------------------------------------------------------------------------------------- |
| `keep`        | `list[str]` | `[]`    | Dotted module names that pruning keeps with everything they import. Anything else raises `wynd: error: [tool.wynd] keep must be a list of module names`. |
| `allow-build` | `bool`      | `false` | Lets uv build packages from source. When it's off, uv installs only wheels, which must be built for Pyodide or be pure Python. |
| `keep-docstrings` | `bool`  | `false` | Ships docstrings in the package modules pruning keeps. Wynd strips them otherwise. See [What Wynd strips](pruning.md#what-wynd-strips-from-kept-files). Anything but `true` or `false` raises `wynd: error: [tool.wynd] keep-docstrings must be true or false`. |

## Runtime SDK

`[project] dependencies` must list exactly one of `crosswind-workers` and
`workers-runtime-sdk`. With neither, or with both, every command that installs
packages exits with `1`. Wynd excludes the other SDK from the install, even
when one of your dependencies requires it, so only one `workers` package
ships.

## Python targets

The `python_workers` compatibility flag and `compatibility_date` choose the
Python version. Wynd checks the rows from the top and takes the first match:

| Python | Chosen when the flags include, or the date is on or after | Pyodide package index              |
| ------ | --------------------------------------------------------- | ---------------------------------- |
| 3.14   | `python_workers_314`, or 2026-09-08                       | `https://index.pyodide.org/314.0.7` |
| 3.13   | `python_workers_20250116`, or 2025-09-29                  | `https://index.pyodide.org/0.28.3` |
| 3.12   | Any other date                                            | `https://index.pyodide.org/0.27.7` |

The config must have `compatibility_date` in `YYYY-MM-DD` form and
`python_workers` in `compatibility_flags`. Otherwise Wynd exits with `1` and
names what it found:

```text
wynd: error: wrangler.jsonc has compatibility_date '2026-09-08' and compatibility_flags []. Add python_workers to compatibility_flags.
```

## When packages reinstall

`sync`, `dev` and the upload commands reinstall `python_modules/` when any of
these changed since the last install:

- The contents of `pyproject.toml` or `pylock.toml`.
- The config file path. For a `cloudflare.config.py`, that is the compiled
  file, so switching modes reinstalls.
- The target: Python version, compatibility date or compatibility flags.
- The Wynd version.

They also reinstall when `python_modules/` is missing, and on every run when a
dependency comes from a local path, because Wynd can't see edits inside it.

A reinstall runs `uv pip compile` against the target's interpreter and Pyodide
index to update `pylock.toml`, then installs the lock into
`.wynd/site-packages/`. Wynd swaps that directory in as `python_modules/` only
after uv succeeds, so a failed install leaves the previous tree in place and
Wrangler doesn't run.

## Config discovery

Without `--config`, Wynd looks for `wrangler.json` in the working directory
and each parent, then for `wrangler.jsonc` the same way, then for
`wrangler.toml`, as Wrangler does. A `wrangler.json` in a parent directory
wins over a `wrangler.toml` in the working directory. Wynd also looks for
`cloudflare.config.py` in the working directory and each parent. If it finds
both kinds, the one in the deeper directory wins, and both in one directory
is an error. Without any config, Wynd exits with
`wynd: error: no config (cloudflare.config.py, wrangler.json, wrangler.jsonc, wrangler.toml) found from <directory>`. `python_modules/` goes
next to the config file. `pyproject.toml` is the nearest one at or above the
working directory, and `.wynd/` goes next to it.

## Environments

With `--env <name>`, Wynd reads the keys from `env.<name>` in the config. A key
set there replaces the top-level value. These keys come from the top level when
the environment leaves them out: `main`, `base_dir`, `compatibility_date`,
`compatibility_flags`, `rules`, `assets`, `build`, `python_modules` and
`logfwdr`. Bindings and `vars` don't, so an environment lists its own.

An unknown environment name exits with `1`. If the config has no `env`
section at all, Wynd uses the top level, as Wrangler does. A
`cloudflare.config.py` has no `env` sections. It takes the name as a
[mode](python-config.md#use-modes) instead.

## Source directory

The source directory is `base_dir`, or the directory of `main`. It must be a
subdirectory of the config's directory, contain `main`, and not contain
`.wynd/`. Otherwise the upload commands exit with:

```text
wynd: error: main and base_dir must point into a subdirectory beside wrangler.jsonc, such as src/, so Wynd can stage the source without the rest of the project.
```

## Staged config

The upload commands write the staged config as `.wynd/build/wrangler.json`,
whatever format your config uses. Wrangler resolves relative paths from the
config's directory. Wynd therefore rewrites these keys to absolute paths into your
project, at the top level, in every `env` section and in `previews`:

- `assets.directory`
- `containers[].image` when it names a file, and `containers[].image_build_context`
- `d1_databases[].migrations_dir` and `d1_databases[].migrations_pattern`
- `build.watch_dir`
- `site.bucket` and `site.entry-point`
- `tsconfig`
- `alias` values that start with `.`

`main` and `base_dir` stay relative, so they point into the staged source.
Wrangler runs from your working directory, so `build.cwd`, `--outdir` and
`--env-file` resolve as they would without Wynd.

A value starting with `./` or `../` under any other key stops the upload,
because it would resolve inside `.wynd/build/`:

```text
wynd: error: wrangler.jsonc has a relative path under unsafe.metadata.path, which Wynd can't point back at the project from .wynd/build/. Use an absolute path.
```

## Files

| Path                     | Written by                  | Commit it | Contents                                                     |
| ------------------------ | --------------------------- | --------- | ------------------------------------------------------------ |
| `cloudflare.config.py`, `pyproject.toml`, `.python-version`, `src/main.py` | `init` | Yes | A new project |
| `pylock.toml`            | Every syncing command       | Yes       | Locked versions for the Pyodide target                       |
| `src/env.py`             | `types`, `dev`, uploads, `init` | Yes       | The generated `Env` class, crosswind-workers only             |
| `python_modules/`        | Every syncing command       | No        | Every installed package, which `wynd dev` runs               |
| `.wynd.wrangler.json`, `.wynd.<mode>.wrangler.json` | Every command, for a `cloudflare.config.py` | No | The [compiled config](python-config.md#check-the-compiled-config) that Wrangler reads |
| `.wynd/synced`           | Every syncing command       | No        | A hash of the install's inputs                               |
| `.wynd/excludes.txt`     | Every syncing command       | No        | The SDK left out of the install                              |
| `.wynd/site-packages/`   | Every syncing command       | No        | An install in progress                                       |
| `.wynd/build/`           | Upload commands             | No        | The pruned source, packages and config Wrangler uploads      |
| `.wynd/shake.txt`        | Upload commands             | No        | Each file's pruning decision                                 |

## Editor environment check

After syncing, `wynd dev` reads the Python version of `.venv` in the project
root. If its minor version differs from the target's, it prints a warning:

```text
warning: .venv is Python 3.13 but wrangler.jsonc targets 3.14 (python_workers, 2026-09-08).
Editor types may differ from production. To match: uv venv --python 3.14 && uv sync
```

The warning doesn't affect what Wynd installs or uploads.
