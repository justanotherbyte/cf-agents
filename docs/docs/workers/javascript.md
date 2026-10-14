---
title: "Call JavaScript APIs from a crosswind-workers Worker"
description: "Use js_object, JavaScript names, wrapper constructors, outbound fetch and import_from_javascript to reach runtime APIs that crosswind-workers doesn't wrap."
---

crosswind-workers wraps the common runtime APIs in Python classes. For
everything else, you call the JavaScript object through
[Pyodide's FFI](https://pyodide.org/en/stable/usage/type-conversions.html).
This page shows the ways to get from a wrapper to JavaScript and back.

## Call a method by its JavaScript name

A wrapper forwards any name its class doesn't define to the JavaScript object.
Arguments and results convert as they do for RPC, so you pass Python values:

```python
await self.ctx.storage.deleteAll()  # same call as delete_all()
meta = await self.env.CACHE.getWithMetadata("key")
```

The name must be the exact JavaScript name. `self.env.CACHE.get_metadata()`
doesn't become `getMetadata()`, and raises `AttributeError`.

On `self.ctx` and on a Durable Object namespace, forwarded calls return raw
JavaScript values, as in the stock SDK. On `self.env` and the binding classes,
they return Python values.

## Get the JavaScript object

Every wrapper has a `js_object` attribute that returns the JavaScript object
it wraps:

| Wrapper                                | `js_object`                     |
| -------------------------------------- | ------------------------------- |
| `Request`                              | JavaScript `Request`            |
| `Response`                             | JavaScript `Response`           |
| `self.env`                             | the JavaScript `env` object     |
| `self.ctx`                             | `ExecutionContext` or `DurableObjectState` |
| `self.env.CACHE` and other bindings    | the JavaScript binding          |
| `Blob`, `File`, `FormData`             | the JavaScript object           |

Pass `js_object` to JavaScript APIs that expect the real object, and to
libraries that don't know about crosswind.

## Wrap a JavaScript object

Every wrapper's constructor accepts the JavaScript object, so you can turn a
raw result back into a Python class:

```python title="src/entry.py"
import js

from workers import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        raw = await js.fetch("https://example.com/")  # a JavaScript Response
        response = Response(raw)
        return Response(f"{response.status} {response.headers['content-type']}")
```

`curl` returns `200 text/html; charset=utf-8`.

Binding classes work the same way. `KVNamespace(js_kv)` wraps a JavaScript KV
namespace, and wrapping a wrapper returns it unchanged.

## Send requests

[`fetch`](api.md#fetch) takes Python keyword arguments and returns a
[`Response`](api.md#response):

```python title="src/entry.py"
from workers import Request, Response, WorkerEntrypoint, fetch


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        upstream = await fetch(
            "https://httpbin.org/post",
            method="POST",
            headers={"content-type": "application/json"},
            body='{"hello": "world"}',
        )
        data = await upstream.json()
        return Response.json({"status": upstream.status, "echo": data["json"]})
```

```json
{"status": 200, "echo": {"hello": "world"}}
```

A network failure, such as a DNS lookup that fails, raises
`pyodide.http.AbortError`, a subclass of `OSError`. An HTTP error status
doesn't raise, so check `upstream.status` or `upstream.ok`.

## Import a runtime module

`import_from_javascript` imports a JavaScript module such as
`cloudflare:sockets`:

```python
from workers import import_from_javascript

sockets = import_from_javascript("cloudflare:sockets")
socket = sockets.connect("example.com:80")
```

`cloudflare:workers` and `cloudflare:sockets` work anywhere, including at the
top of a module and on Python 3.12. Other modules need Python 3.13 or later,
and raise `ImportError` on 3.12.

## Convert values yourself

Import `to_js` and `create_proxy` from `pyodide.ffi`. crosswind doesn't
re-export them. A JavaScript object built from a `dict` needs
`dict_converter=js.Object.fromEntries`, or JavaScript receives a `Map`:

```python
import js
from pyodide.ffi import to_js

options = to_js({"cacheTtl": 60}, dict_converter=js.Object.fromEntries)
```

[`python_to_rpc`](api.md#rpc-conversion) and
[`python_from_rpc`](api.md#rpc-conversion) apply the same rules the runtime
applies to RPC arguments and results.
