---
title: "Migrate a Python Worker from workers-runtime-sdk to crosswind-workers"
description: "Swap workers-runtime-sdk for crosswind-workers, add types one class at a time, and check the behaviour differences between the two SDKs."
---

Your Worker's code doesn't change when you switch SDKs. crosswind-workers has
every name the stock `workers` package has, with the same signatures. This
page swaps the dependency, adds types, and lists where the two SDKs behave
differently.

## Before you start

The project must build with [Wynd](../wynd/overview.md). pywrangler always
installs `workers-runtime-sdk`, and with both SDKs installed, crosswind-workers
refuses to import. If you still use pywrangler, follow
[Switch from pywrangler](../wynd/getting-started.md) first.

## 1. Swap the dependency

```sh
uv remove workers-runtime-sdk
uv add crosswind-workers
```

`pyproject.toml` now lists `crosswind-workers` and not
`workers-runtime-sdk`. Wynd stops with an error if it finds both.

## 2. Run the Worker

```sh
uvx wynd dev
```

Wynd sees the changed `pyproject.toml`, reinstalls `python_modules/`, and
writes `src/env.py`. Send the requests you normally test with. They return
what they returned on the stock SDK, except for the cases under
[Differences from the stock SDK](#differences-from-the-stock-sdk).

## 3. Add types

Import `Env` and pass it to each entrypoint:

```python
from env import Env
from workers import DurableObject, WorkerEntrypoint


class Counter(DurableObject[Env]): ...


class Default(WorkerEntrypoint[Env]): ...
```

`self.env` now has a type, and camelCase calls keep working while you move to
the typed methods. Change them one at a time:

| Stock SDK call                                   | Typed call                                          |
| ------------------------------------------------ | --------------------------------------------------- |
| `self.env.KV.put("k", "v", expirationTtl=60)`    | `self.env.KV.put("k", "v", expiration_ttl=60)`      |
| `self.env.COUNTER.getByName("x")`                | `self.env.COUNTER.get_by_name("x")`                 |
| `self.env.COUNTER.idFromName("x")`               | `self.env.COUNTER.id_from_name("x")`                |
| `self.ctx.storage.deleteAll()`                   | `self.ctx.storage.delete_all()`                     |
| `self.ctx.storage.setAlarm(ms)`                  | `self.ctx.storage.set_alarm(datetime_or_ms)`        |
| `self.ctx.waitUntil(coro)`                       | `self.ctx.wait_until(coro)`                         |
| `self.ctx.acceptWebSocket(ws)`                   | `self.ctx.accept_web_socket(ws)`                    |

Change `waitUntil` first in a Durable Object. There, `self.ctx.waitUntil(coro)`
returns without an error, but the coroutine never runs. The `wynd dev` log
shows `Uncaught Error: This borrowed proxy was automatically destroyed at the
end of a function call`. Only `self.ctx.wait_until(coro)` accepts a coroutine.

Bindings with no typed class keep their JavaScript names. See
[Use bindings](bindings.md#keyword-options-and-javascript-names).

## 4. Replace private attributes

The stock SDK has no public way to reach the JavaScript object behind `env`,
`ctx` or a binding, so some code reads its private attributes. crosswind
answers `_env`, `_ctx` and `_binding` with the JavaScript object, so that code
runs. Replace them with `js_object`, which works on every wrapper:

```python
raw_ctx = self.ctx.js_object  # was self.ctx._ctx
raw_kv = self.env.CACHE.js_object  # was self.env.CACHE._binding
```

## Generic subclasses

The stock SDK's `DurableObject` doesn't call `super().__init_subclass__()`, so
a library that mixes in `Generic` has to list it first. crosswind works with
either order at runtime. Type checkers treat `DurableObject` as generic, so
put `Generic` last in typed code:

```python
from typing import Generic, TypeVar

from workers import DurableObject

S = TypeVar("S")


class Agent(DurableObject, Generic[S]): ...
```

## Differences from the stock SDK

Each row is a stock SDK bug that crosswind fixes. The first ten raised an
exception on the stock SDK, so working code doesn't depend on them. The last
four changed a value or a body without an error, so check code that touches
them.

| Case                                                        | Stock SDK                         | crosswind-workers                             |
| ----------------------------------------------------------- | --------------------------------- | --------------------------------------------- |
| `File(js_file)` for a JavaScript `File` received over RPC   | Raises                            | Wraps it, keeping its name and time            |
| `len(form_data)`                                            | `TypeError`                       | The number of entries                         |
| `Response(blob)`                                            | `TypeError`                       | A response with the blob as its body          |
| `request.method` for a non-standard method such as `PURGE`  | `KeyError`                        | The method as a `str`                         |
| A `memoryview` or `bytearray` as a `Blob` part              | Raises                            | Accepted                                      |
| A non-JavaScript exception inside `step.do`                 | `AttributeError`                  | The original exception                        |
| A Workflow step named `"ctx-step"` as a dependency          | Can't be named, so `TypeError`    | Named by its function's name                  |
| `env` or a Durable Object namespace sent over RPC           | Raises                            | Sent as its JavaScript object                 |
| `with patch_env(...)`                                       | `AttributeError`                  | `NotImplementedError` that explains why       |
| A request body read through `wsgi.input` on Python 3.12, which has no `run_sync` | `ImportError` | The adapter reads the body up front |
| `form_data["missing"]`                                      | Returns JavaScript `null`         | `KeyError`                                    |
| `Response.redirect(url)`                                    | A raw JavaScript `Response`       | A crosswind `Response`                        |
| A binding attribute that changes, such as a D1 cursor count | First value cached                | Read again on every access                    |
| `Content-Encoding` set by the app in the ASGI or WSGI adapter | Body compressed a second time   | Body sent as the app encoded it               |

Code that used the raw JavaScript object from `Response.redirect` keeps
working, because camelCase names pass through and `js_object` returns it.

`patch_env` swapped `env` inside a `with` block. It relied on a runtime helper
that workerd removed on 2026-06-30, so it fails on both SDKs. Pass the values
you need as arguments instead.
