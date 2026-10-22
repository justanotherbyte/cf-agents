---
title: "Configure a Python Worker in cloudflare.config.py"
description: "Write a Worker's config as Python with wynd.config: create one with wynd init, declare bindings, Durable Objects and triggers, pick settings per mode, check the compiled Wrangler config and fix its errors."
---

Wynd can read the Worker's config from a Python file, `cloudflare.config.py`,
in place of `wrangler.toml` or `wrangler.jsonc`. Use it for a new
[crosswind-workers](../workers/overview.md) project, which `wynd init`
creates with one. Keep your Wrangler file if it uses a binding or setting
that `wynd.config` doesn't model, listed under
[What it doesn't support](#what-it-doesnt-support), or if you also run
plain `wrangler` on the project. Wynd compiles the Python file into a Wrangler
config and runs Wrangler on that, so `wynd dev`, `wynd deploy` and
`wynd types` work the same with either file.

What the Python file adds:

- Values are typed dataclasses, so your editor and type checker catch a
  misspelled field or a missing argument before Wynd runs.
- One function returns the config for each mode, such as `staging`, in place
  of Wrangler's `env` sections.
- You declare a Durable Object class once, by its state, and Wynd writes
  Wrangler's `exports` map. You don't write `migrations`.

> **Warning**
>
> Python config support is new. Its classes and fields may change between
> minor releases.

## Before you begin

- Wynd 0.3.0 or later. `uvx wynd` runs the latest release, and
  `uvx wynd@0.3.0` pins it.
- [uv](https://docs.astral.sh/uv/) 0.12.3 or later. Check with `uv --version`.
- Node.js on `PATH`, for `npx`. Wynd runs Wrangler 4.129.0 through it.

## Create a project

```sh
uvx wynd init hello
```

```text
wynd: wrote src/env.py
wynd: created hello. Run `wynd dev` in /home/you/hello to serve it.
```

`wynd init [directory]` writes into `directory`, or into the current
directory without one. The Worker's name is the directory name in lowercase,
with each run of other characters replaced by `-`. It writes:

| File                   | Contents                                                                 |
| ---------------------- | ------------------------------------------------------------------------ |
| `cloudflare.config.py` | The Worker's config                                                      |
| `pyproject.toml`       | `crosswind-workers` in `dependencies`, and `wynd>=0.3.0` in the `dev` group |
| `.python-version`      | `3.14`, the Python the Worker runs                                        |
| `src/main.py`          | A `Default` entrypoint that returns `Hello from Python!`                  |
| `src/env.py`           | The generated `Env` class                                                 |
| `.gitignore`           | `.venv/`, `python_modules/`, `.wynd/` and `.wynd.*wrangler.json`, added to an existing file |

The config sets the Worker's name, entry module and compatibility date:

```python title="cloudflare.config.py"
from wynd.config import Config, Worker

config = Config(
    worker=Worker(
        name="hello",
        entrypoint="src/main.py",
        compatibility_date="2026-09-08",
    ),
)
```

`entrypoint` is Wrangler's `main`. Wynd adds the `python_workers`
compatibility flag itself, so `compatibility_flags` lists only extra flags.

`wynd init` stops without writing anything if the directory already has any
of these files, or a `wrangler.json`, `wrangler.jsonc` or `wrangler.toml`:

```text
wynd: error: /home/you/hello already has src/main.py, wrangler.toml. wynd init only writes a new project.
```

Run the Worker:

```sh
cd hello
uvx wynd dev
```

```text
 ⛅️ wrangler 4.129.0
...
[wrangler:info] Ready on http://localhost:8787
```

In another terminal, `curl http://localhost:8787` prints
`Hello from Python!`.

Then run `uv sync`. It creates `.venv` on Python 3.14 and installs Wynd from
the `dev` group, so your editor resolves `wynd.config`. Wynd itself doesn't
need it, because `uvx wynd` runs the config with its own copy. Wynd never
installs the `dev` group into `python_modules/`, so it doesn't reach the
Worker.

## Add bindings

`env` maps each binding name to a binding from `wynd.config.bindings`. This
config adds a text and a JSON variable, a secret, KV, D1 and a Durable
Object, plus a cron trigger:

```python title="cloudflare.config.py"
from wynd.config import Config, Worker, bindings, exports, triggers


def config(mode):
    staging = mode == "staging"
    suffix = "-staging" if staging else ""
    return Config(
        worker=Worker(
            name=f"counter{suffix}",
            entrypoint="src/main.py",
            compatibility_date="2026-09-08",
            env={
                "GREETING": bindings.Text("hello"),
                "LIMITS": bindings.Json({"uploads": 10, "users": 3}),
                "API_TOKEN": bindings.Secret(),
                "CACHE": bindings.KV(id="<your KV namespace id>"),
                "DATABASE": bindings.D1(name=f"counter-db{suffix}"),
                "COUNTER": bindings.DurableObject("Counter"),
                "DEBUG": staging and bindings.Text("1"),
            },
            exports={"Counter": exports.DurableObject()},
            triggers=[triggers.Cron("0 * * * *")],
        ),
    )
```

```python title="src/main.py"
from env import Env
from workers import DurableObject, Response, WorkerEntrypoint


class Counter(DurableObject[Env]):
    async def increment(self) -> int:
        value = (await self.ctx.storage.get("value") or 0) + 1
        await self.ctx.storage.put("value", value)
        return value


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request):
        value = await self.env.COUNTER.get_by_name("main").increment()
        return Response(f"{self.env.GREETING}: {value}")
```

`config` here is a function of the mode. A config with no modes sets
`config = Config(...)`, as `wynd init` writes it. A `None` or `False` value
leaves the binding out, so `"DEBUG": staging and bindings.Text("1")` exists
only in staging.

`bindings.DurableObject("Counter")` names a class in `exports`, and
`exports.DurableObject()` declares that class with SQLite storage. Wynd
compiles both into the Wrangler config, so the Worker needs no
`migrations`.

Set the secret's local value in `.dev.vars`, beside `cloudflare.config.py`:

```text title=".dev.vars"
API_TOKEN=local-token
```

Run it in staging mode:

```sh
uvx wynd dev -e staging
```

Wrangler lists the staging bindings, including `DEBUG`, and two requests to
`http://localhost:8787` print `hello: 1` and `hello: 2`:

```text
Using secrets defined in .dev.vars
Your Worker has access to the following bindings:
Binding                                       Resource                  Mode
env.COUNTER (Counter)                         Durable Object            local
env.CACHE (<your KV namespace id>)            KV Namespace              local
env.DATABASE (counter-db-staging)             D1 Database               local
env.GREETING ("hello")                        Environment Variable      local
env.LIMITS ({"uploads":10,"users":3})         Environment Variable      local
env.DEBUG ("1")                               Environment Variable      local
env.API_TOKEN ("(hidden)")                    Environment Variable      local
```

Without `.dev.vars`, Wrangler warns
`Missing required secrets: API_TOKEN.` and the Worker starts without it.
For production, set the secret with `uvx wynd secret put API_TOKEN`.

### Bindings

Each class compiles to one Wrangler entry, with the dictionary key as the
binding name:

| Class                                        | Wrangler entry                       |
| -------------------------------------------- | ------------------------------------ |
| `Text(value)`                                | `vars`                               |
| `Json(value)`                                | `vars`, with any JSON value          |
| `Secret()`                                   | `secrets.required`                   |
| `KV(id=None)`                                | `kv_namespaces`                      |
| `D1(name=None, id=None)`                     | `d1_databases`, as `database_name` and `database_id` |
| `R2(name=None, jurisdiction=None)`           | `r2_buckets`, as `bucket_name`. `jurisdiction` is `"eu"` or `"fedramp"` |
| `Queue(name, delivery_delay=None)`           | `queues.producers`                   |
| `DurableObject(export, worker=None)`         | `durable_objects.bindings`. Without `worker`, `export` must be a live Durable Object in this Worker's `exports` |
| `Workflow(export, worker=None, name=None)`   | `workflows`. Without `worker`, `export` must be an `exports.Workflow`. With `worker`, `name` is required |
| `Service(worker, export=None, props=None)`   | `services`. `export` names an entrypoint of the other Worker |
| `AI()`                                       | `ai`, one per Worker                 |
| `VersionMetadata()`                          | `version_metadata`, one per Worker   |
| `Assets()`                                   | `assets.binding`, one per Worker. Needs `Worker.assets` |

`wynd types` types a `Secret()` only when its name is in `.dev.vars`, as it
does for a Wrangler config. See
[Generate typed bindings](types.md#a-secret-is-missing-from-env).

### Exports

`exports` maps each class name in the entry module to its kind. Durable
Object classes take one of five states:

| Class                                          | Declares                                    |
| ---------------------------------------------- | ------------------------------------------- |
| `exports.DurableObject(storage="sqlite", container=None)` | A Durable Object class the Worker runs. `storage` is `"sqlite"` or `"legacy-kv"` |
| `exports.DeletedDurableObject()`               | A class you removed, whose data Cloudflare deletes |
| `exports.RenamedDurableObject(renamed_to=...)` | A class you renamed. `renamed_to` must be a live Durable Object in `exports` |
| `exports.TransferredDurableObject(transferred_to=...)` | A class you moved to another Worker |
| `exports.ExpectingTransferDurableObject(transfer_from=..., storage="sqlite", container=None)` | A class this Worker receives from another Worker |
| `exports.Worker(cache=None)`                   | A `WorkerEntrypoint` class, with `cache` as `cache.enabled` |
| `exports.Workflow(name, limits_steps=None)`    | A `WorkflowEntrypoint` class. It needs exactly one `bindings.Workflow` in `env` |

A `Container` goes on the Durable Object export that runs it, and needs
`storage="sqlite"`:

```python
from wynd.config import Container, exports

image = Container(name="image", dockerfile="./Dockerfile", max_instances=3)
exports.DurableObject(container=image)
```

`Container` takes exactly one of `dockerfile` and `image`, a prebuilt image
reference. `build_context` and `build_vars` need `dockerfile`. Its other
fields are `instance_type` and `max_instances`. Paths are relative to
`cloudflare.config.py`.

> **Warning**
>
> Wrangler 4.129.0 uploads the `exports` map in place of `migrations`.
> `wynd dev` runs Durable Objects declared this way, but no Wynd test deploys
> them to Cloudflare yet. Before you move a deployed Worker whose Wrangler
> config has `migrations` to `cloudflare.config.py`, deploy the change to a
> copy of the Worker first. A wrong state can delete a class's data.

### Triggers and other settings

`triggers` takes `triggers.Cron(schedule)`,
`triggers.Fetch(pattern, zone=None)` for a route, and
`triggers.Queue(name, ...)` for a queue consumer, with
`max_batch_size`, `max_batch_timeout`, `max_retries`, `max_concurrency`,
`retry_delay` and `dead_letter_queue`.

Wynd always writes `triggers.crons`, as `[]` when the config has no `Cron`.
Wrangler clears the deployed Worker's cron triggers when that list is empty,
so a cron trigger you add in the dashboard goes away on the next deploy.
Declare every cron trigger in the config.

`Worker` also takes these fields, each compiled to the Wrangler key of the
same name unless the table says otherwise:

| Field                 | Type                                             |
| --------------------- | ------------------------------------------------ |
| `base_dir`            | `str`                                            |
| `compatibility_flags` | `list[str]`, added after `python_workers`        |
| `domains`             | `list[str]`, compiled to `routes` with `custom_domain: true` |
| `assets`              | `Assets(directory, html_handling=None, not_found_handling=None, run_worker_first=None)` |
| `observability`       | `Observability(enabled=None, head_sampling_rate=None, redact_query_string=None, logs=None, traces=None)` |
| `placement`           | `Placement(mode, hint=None)`, with `mode` as `"off"` or `"smart"` |
| `limits`              | `Limits(cpu_ms=None, subrequests=None)`          |
| `logpush`, `workers_dev`, `preview_urls` | `bool`                        |
| `tail_consumers`      | `list[TailConsumer(worker)]`, compiled to `tail_consumers[].service` |

`Config` takes `worker`, plus `account_id` and `compliance_region`, which is
`"public"` or `"fedramp-high"`. Import the settings classes from
`wynd.config`.

## Use modes

The mode is the value of `-e` or `--env`, or of `$CLOUDFLARE_ENV` when you
pass neither. Wynd calls `config(mode)` with it, and with `None` when there
is none:

```sh
uvx wynd dev                  # config(None)
uvx wynd deploy -e staging    # config("staging")
```

Wrangler never sees the mode. Wynd removes `-e` and `--env` from the
arguments and runs Wrangler without `CLOUDFLARE_ENV`, because the compiled
file has no `env` sections. Wrangler therefore doesn't append the mode to
the Worker's name, so name staging Workers yourself, as the example does
with `suffix`. It also reads `.dev.vars` in every mode, and never
`.dev.vars.staging`.

A mode may hold only letters, digits, `_` and `-`, because it becomes part of
a file name.

`src/env.py` holds the bindings of the last mode a command compiled. If modes
have different bindings, run `uvx wynd types` in the mode you check types
against before you commit.

## Check the compiled config

Wynd compiles `cloudflare.config.py` into a Wrangler file in the same
directory:

| Mode      | Compiled file                    |
| --------- | -------------------------------- |
| No mode   | `.wynd.wrangler.json`            |
| `staging` | `.wynd.staging.wrangler.json`    |

Every Wynd command compiles it first, passthrough commands such as
`wynd whoami` included, so every command runs your config's Python. Wynd
rewrites the file only when its text changes, so a running `wynd dev`
doesn't reload when nothing changed. The `.gitignore` that `wynd init` writes
lists `.wynd.*wrangler.json`, so these files stay out of Git.

Because the compiled file sits beside `cloudflare.config.py`,
`python_modules/`, `.dev.vars`, `.wrangler/` and relative paths resolve from
the same directory as they would for a `wrangler.jsonc` there. Wrangler's own
config search doesn't look for these names, so plain `npx wrangler` in the
directory ignores them.

To see what Wrangler gets, run any command and open the file:

```sh
uvx wynd types -e staging
cat .wynd.staging.wrangler.json
```

```json title=".wynd.staging.wrangler.json"
{
  "name": "counter-staging",
  "main": "src/main.py",
  "compatibility_date": "2026-09-08",
  "compatibility_flags": [
    "python_workers"
  ],
  "exports": {
    "Counter": {
      "type": "durable-object",
      "storage": "sqlite"
    }
  },
  "vars": {
    "GREETING": "hello",
    "LIMITS": {
      "uploads": 10,
      "users": 3
    },
    "DEBUG": "1"
  },
  "secrets": {
    "required": [
      "API_TOKEN"
    ]
  },
  "kv_namespaces": [
    {
      "binding": "CACHE",
      "id": "<your KV namespace id>"
    }
  ],
  "d1_databases": [
    {
      "binding": "DATABASE",
      "database_name": "counter-db-staging"
    }
  ],
  "durable_objects": {
    "bindings": [
      {
        "name": "COUNTER",
        "class_name": "Counter"
      }
    ]
  },
  "triggers": {
    "crons": [
      "0 * * * *"
    ]
  }
}
```

`uvx wynd deploy --dry-run -e staging` then stages the upload from this file
into `.wynd/build/wrangler.json`, as for any Wrangler config, and Wrangler
prints the bindings it would deploy.

## How Wynd finds the config

Without `--config`, Wynd looks for `cloudflare.config.py` in the working
directory and each parent, and runs Wrangler's own search for
`wrangler.json`, `wrangler.jsonc` and `wrangler.toml`. The file in the deeper
directory wins. If one directory has both kinds, every command stops:

```text
wynd: error: /home/you/hello has cloudflare.config.py and wrangler.jsonc. Keep one.
```

`--config cloudflare.config.py` names the file directly.

The file runs with its own directory on `sys.path`, so it can import a
helper module beside it. It can't import your Worker code, which needs the
Workers runtime.

## Move a Wrangler config to Python

1. Write `cloudflare.config.py` beside your Wrangler file, with the same
   name, `main` as `entrypoint`, date, flags and bindings. Write each `env`
   section as a branch on the mode, and each Durable Object class in
   `migrations` as its state in `exports`.
2. Delete the Wrangler file, since Wynd refuses a directory with both.
3. Add `.wynd.*wrangler.json` to `.gitignore`.
4. Run `uv add --dev wynd`, so your editor resolves `wynd.config`.
5. Run `uvx wynd deploy --dry-run` for each mode, and compare Wrangler's
   bindings list with the one your Wrangler file gave.

Read the Durable Object warning under [Exports](#exports) before you deploy.

## Errors

Each error stops the command before Wrangler runs, and the command exits
with `1`.

| Cause | How to trigger it | Message |
| --- | --- | --- |
| A value fails a check inside one object | `compatibility_date="soon"` | `wynd: error: cloudflare.config.py:4: compatibility_date 'soon' isn't a YYYY-MM-DD date` |
| A misspelled or missing argument | `Worker(nmae="hello", ...)` | `wynd: error: cloudflare.config.py:4: Worker.__init__() got an unexpected keyword argument 'nmae'` |
| Rules that span objects fail | A binding to a class missing from `exports`, and a second `AI()` | `wynd: error: cloudflare.config.py has errors:` followed by one line per rule, such as `env['COUNTER']: exports has no live Durable Object named 'Counter'` and `env['AI2']: a Worker has one AI binding` |
| No `config` | The file names its value `settings` | ``wynd: error: cloudflare.config.py must set `config` to a Config, or to a function that takes the mode and returns one`` |
| A value JSON can't hold | `bindings.Json({"a", "b"})`, a set | `wynd: error: cloudflare.config.py holds a value JSON can't encode: Object of type set is not JSON serializable` |
| A mode with other characters | `-e eu.west` | `wynd: error: mode 'eu.west' may hold only letters, digits, _ and -` |
| Both config kinds in one directory | A `wrangler.jsonc` beside `cloudflare.config.py` | `wynd: error: <directory> has cloudflare.config.py and wrangler.jsonc. Keep one.` |

The line number is the line in `cloudflare.config.py` where the failing call
starts, here `worker=Worker(`. Wynd reports every rule that spans objects in
one message, so you can fix them all at once.

Any other exception in your config, such as a `NameError`, prints Python's
traceback, which ends at the line in `cloudflare.config.py`.

Errors Wynd finds after compiling, such as a bad source directory, name the
compiled file, for example
`main and base_dir must point into a subdirectory beside .wynd.wrangler.json`.
Fix them in `cloudflare.config.py`. [Troubleshoot Wynd errors](troubleshooting.md)
lists them.

## What it doesn't support

- Binding kinds other than those in [Bindings](#bindings), such as
  Vectorize, Hyperdrive, Browser Rendering, rate limits and Analytics Engine.
  Keep a Wrangler file for a Worker that needs one.
- Wrangler's `migrations`, `env` sections, `unsafe` and per-binding `dev`
  settings.
- `.dev.vars.<mode>` files. Wrangler reads `.dev.vars` in every mode.
- Reloading on a config change. After you edit `cloudflare.config.py`,
  stop `wynd dev` and start it again.
- Importing one `cloudflare.config.py` from another, since the dotted file
  name can't be imported.
- Running without Wynd. Plain `wrangler` doesn't read `cloudflare.config.py`.
