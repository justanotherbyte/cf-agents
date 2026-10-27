---
title: "Troubleshoot Wynd errors"
description: "Causes and fixes for Wynd errors: missing config or SDK, cloudflare.config.py errors, missing uv, Python target errors, uv install failures, staging and path errors, and refused Wrangler flags."
---

Wynd's own errors start with `wynd: error:` and stop the command before
Wrangler runs. Search this page for the text after that prefix. Errors from
pruning are on [Prune the upload](pruning.md#troubleshooting), errors
from `wynd types` are on
[Generate typed bindings](types.md#troubleshooting), and each error that
`cloudflare.config.py` can raise is on
[Write config in Python](python-config.md#errors).

## `no config (cloudflare.config.py, wrangler.json, wrangler.jsonc, wrangler.toml) found`

**Cause.** No directory from the working directory up to the root has a
config file.

**Fix.** Run Wynd from the project directory, pass `--cwd <project>`, or
create a project with `uvx wynd init`.

## `no pyproject.toml in <directory> or any parent directory`

**Cause.** Wynd reads dependencies and `[tool.wynd]` from the nearest
`pyproject.toml`, and found none.

**Fix.** Run Wynd from the project, or add a `pyproject.toml` that lists
`crosswind-workers` or `workers-runtime-sdk`.

## `has cloudflare.config.py and wrangler.jsonc. Keep one.`

**Cause.** One directory has both a `cloudflare.config.py` and a Wrangler
file, so Wynd can't tell which one you mean. Every command stops, passthrough
commands included.

**Fix.** Delete the file you don't use. If you're
[moving to `cloudflare.config.py`](python-config.md#move-a-wrangler-config-to-python),
check its compiled output first, then delete the Wrangler file.

## `cloudflare.config.py has errors:` or `cloudflare.config.py:<line>:`

**Cause.** The Python config failed a check. A message with a line number
comes from one object, such as a malformed `compatibility_date`. A list under
`has errors:` comes from rules across objects, such as a binding to a class
missing from `exports`.

**Fix.** See [Write config in Python](python-config.md#errors) for each
message and the change that triggers it.

## `already has <files>. wynd init only writes a new project.`

**Cause.** `wynd init` found a project file, such as `pyproject.toml`,
`src/main.py` or a Wrangler config, in the target directory. It writes
nothing.

**Fix.** Run `uvx wynd init <new-directory>`, or move the files away.

## `pyproject.toml declares neither crosswind-workers nor workers-runtime-sdk`

**Cause.** Wynd installs only the runtime SDK your project lists, and
`[project] dependencies` lists neither. pywrangler projects often list no SDK,
because pywrangler adds one itself.

**Fix.** Add one:

```sh
uv add workers-runtime-sdk  # or: uv add crosswind-workers
```

## `pyproject.toml declares both crosswind-workers and workers-runtime-sdk`

**Cause.** Both SDKs provide the `workers` package, so only one can ship.

**Fix.** Remove the one you don't use with `uv remove`. A dependency that
requires the other SDK doesn't count. Wynd leaves that SDK out of the install.

## `Add python_workers to compatibility_flags.`

The full message names the config, date and flags Wynd found:

```text
wynd: error: wrangler.jsonc has compatibility_date '2026-09-08' and compatibility_flags []. Add python_workers to compatibility_flags.
```

**Cause.** Without the `python_workers` flag, the config doesn't describe a
Python Worker, so Wynd can't choose a Python version. The same message ends
with `Set a compatibility_date.` or `Write the date as YYYY-MM-DD.` when the
date is missing or malformed.

**Fix.** Add the flag or the date. If you pass `--env`, check that
environment's section, because a `compatibility_flags` key there replaces the
top-level list.

## `Wynd needs uv 0.13.4 or later`

**Cause.** The `uv` on `PATH` is older than 0.13.4, the oldest version Wynd
supports. Without any `uv` on `PATH`, the message is
`uv not found on PATH. Install uv 0.13.4 or later.`

**Fix.** Run `uv self update`, or install uv again from
[its docs](https://docs.astral.sh/uv/getting-started/installation/).

## `npx not found on PATH. Install Node.js, which provides npx.`

**Cause.** Wynd runs Wrangler with `npx`, which comes with Node.js.

**Fix.** Install Node.js, and check that `npx --version` works in the same
shell.

## `uv pip compile failed:`

The output after this line is uv's own error. The common one:

```text
wynd: error: uv pip compile failed:
error: No solution found when resolving dependencies
  cause: Because all versions of psycopg2 have no usable wheels and badpkg depends on psycopg2, we can conclude that your requirements are unsatisfiable.
```

**Cause.** The package has no wheel for the Worker's Python target. Wynd
installs from the [Pyodide package index](commands.md#python-targets) for the
target and from PyPI. A package with compiled code needs a wheel built for
Pyodide.

**Fix.**

1. Look for the package on the target's Pyodide index. Some packages have a
   wheel for one Python version and not another, so a different
   compatibility date may work.
2. If the package is pure Python but ships only a source archive, set
   `allow-build = true` under [`[tool.wynd]`](commands.md#toolwynd).
3. Otherwise, replace the package. `psycopg2`, for example, has no Pyodide
   build.

## `--compatibility-date, --compatibility-flags and --latest change the Python version after Wynd installs packages`

**Cause.** You passed one of these flags to `dev` or an upload command.
Wrangler would apply it after Wynd installed packages for the config's
version.

**Fix.** Set `compatibility_date` and `compatibility_flags` in the config,
or in an environment you select with `--env`.

## `main and base_dir must point into a subdirectory beside wrangler.jsonc`

**Cause.** The upload commands copy your source directory into
`.wynd/build/`. That fails when `main` sits next to the config, or when
`base_dir` points at the project root or outside it.

**Fix.** Move your code into `src/` and set `"main": "src/entry.py"`. With a
`cloudflare.config.py`, the message names `.wynd.wrangler.json`, and the fix
is `entrypoint="src/main.py"` or another path under a subdirectory.

## `has a relative path under <key>, which Wynd can't point back at the project`

**Cause.** A config value starts with `./` or `../` under a key Wynd doesn't
rewrite. From `.wynd/build/`, it would point at the wrong place.
[Staged config](commands.md#staged-config) lists the keys Wynd rewrites.

**Fix.** Make the path absolute.

## `<config> has no environment named 'staging'`

**Cause.** `--env` or `$CLOUDFLARE_ENV` names an environment that isn't in
the config's `env` section.

**Fix.** Check the name, and check that `CLOUDFLARE_ENV` isn't set from
another project.
