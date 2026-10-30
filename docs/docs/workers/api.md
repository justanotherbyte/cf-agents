---
title: "crosswind-workers API reference"
description: "Every public class, method and function in crosswind-workers: entrypoints, context, Request, Response, fetch, Blob, FormData, binding classes, RPC conversion, Workflows and the ASGI and WSGI adapters."
---

Use this reference to find the runtime classes, methods and functions in
`crosswind-workers`. Import names from
`workers` unless an entry names another module.

Await every method marked `async`. Every wrapper class has a
`js_object` attribute that returns the JavaScript object it wraps. A wrapper
forwards any name it doesn't define to that object under its JavaScript name.
[Call JavaScript APIs](javascript.md) explains the forwarding rules.

## Entrypoints

The runtime creates these classes and calls their methods. Each is generic
over an optional `Env` type for type checkers only. At runtime,
`WorkerEntrypoint[Env]` is `WorkerEntrypoint`, so `Env` can be any class,
including the `Protocol` that [`wynd types`](../wynd/types.md) writes.

| Class                | `self.ctx`                                       | Handlers the runtime calls                                                  |
| -------------------- | ------------------------------------------------ | --------------------------------------------------------------------------- |
| `WorkerEntrypoint`   | [`ExecutionContext`](#executioncontext)          | `fetch(request)`, `scheduled(controller, env, ctx)`, `queue(batch, env, ctx)`, `connect`, and your own RPC methods |
| `DurableObject`      | [`DurableObjectContext`](#durableobjectcontext)  | `fetch`, `alarm`, `webSocketMessage`, `webSocketClose`, `webSocketError`, `connect`, and RPC methods |
| `WorkflowEntrypoint` | The JavaScript context, unwrapped                | `run(event, step)`                                                          |

All three take `(ctx, env)` in `__init__` and set `self.ctx` and `self.env`.
If you override `__init__`, call `super().__init__(ctx, env)` before you read
`self.ctx` or `self.env`. The `ctx` and `env` your `__init__` receives are
already wrapped. `self.env` returns each binding as one of the
[binding classes](#bindings) below.

Arguments the runtime passes to `connect`, `queue`, `alarm`, the
`webSocket*` handlers and `run` arrive as Python values or wrappers. A binary
WebSocket message stays a JavaScript `ArrayBuffer`.

`queue` and `scheduled` must accept three arguments after `self`. The runtime
passes `env` and `ctx` positionally as `None`, so read `self.env` and
`self.ctx` instead. A `queue(self, batch)` fails on every batch with
`TypeError: Default.queue() takes 2 positional arguments but 4 were given`.
In `queue`, `batch.messages` is a list of messages. Each `message.body` is a
Python value, and `message.ack()` acknowledges it.

On compatibility dates before 2025-08-14 the runtime calls `on_fetch` and
`on_connect`, plus `on_alarm` and `on_webSocket*` on Durable Objects.
crosswind adds those names as aliases of the plain ones. If your class defines
an `on_<name>` method itself, the runtime calls yours.

```python
from workers import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        return Response("hello")
```

### `handler`

`handler(func)` decorates a module-level `on_fetch(request, env, ctx)`
function, which the runtime calls only on dates before 2025-08-14. It wraps
the JavaScript request in a [`Request`](#request) and the env in the env
wrapper. Use a `WorkerEntrypoint` subclass instead on any date.

## Context

### `ExecutionContext`

`self.ctx` on a `WorkerEntrypoint`. Import it from `workers.context` for type
annotations.

| Member                          | Description                                                                                       |
| ------------------------------- | ------------------------------------------------------------------------------------------------- |
| `wait_until(awaitable)`         | Keeps the invocation alive until `awaitable`, a coroutine, task or JavaScript promise, finishes. Returns `None`. |
| `pass_through_on_exception()`   | On an uncaught exception, sends the request to the origin as if the Worker weren't there.         |

Other names, such as `waitUntil` and `props`, return the raw JavaScript value.

### `DurableObjectContext`

`self.ctx` on a `DurableObject`. Import it from `workers.context`.

| Member                                      | Description                                                                                                              |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `storage`                                   | The object's [`DurableObjectStorage`](#durableobjectstorage).                                                            |
| `id`                                        | The object's JavaScript `DurableObjectId`.                                                                               |
| `wait_until(awaitable)`                     | As on `ExecutionContext`.                                                                                                |
| `async block_concurrency_while(callback)`   | Runs `callback`, an async function, while no other event reaches the object. Returns its result. From `__init__`, call the JavaScript name `blockConcurrencyWhile` without `await`. |
| `accept_web_socket(ws, tags=None)`          | Accepts a WebSocket for hibernation. `tags` is a `list[str]` for `get_web_sockets` to filter on.                          |
| `get_web_sockets(tag=None)`                 | The accepted WebSockets, each a [`Binding`](#binding), filtered by `tag` if given.                                       |
| `abort(reason=None)`                        | Resets the object, then raises `DurableObjectAbort` to stop the current call.                                            |

`DurableObjectAbort` derives from `BaseException`, so `except Exception`
doesn't catch it. Import it from `workers.entrypoints` or `workers.context`.
The caller's `await` raises `pyodide.ffi.JsException` with the reason, such
as `Error: bye` for `abort("bye")`. The next call to that name reaches a new
instance, so attributes set on `self` start over.

### `DurableObjectStorage`

Every method is `async`, takes Python values and returns Python values. Each
also accepts the JavaScript options object as a dictionary in the `options`
position, and forwards unknown keywords into it.

| Method                                                                                  | Returns                                                         |
| --------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `get(keys, options=None, *, allow_concurrency=None, no_cache=None)`                     | The value for one key, `None` if missing, or a `dict` for a list of keys that leaves out missing keys. |
| `put(key, value, options=None, *, allow_concurrency=None, allow_unconfirmed=None, no_cache=None)` | `None`. Also `put(entries, options=None)` with a `dict` of entries. Raises `TypeError` for `put(key)` with no value. |
| `delete(keys, options=None, *, allow_unconfirmed=None, no_cache=None)`                  | `True` if one key existed, or the count for a list of keys.     |
| `delete_all(options=None)`                                                              | `None`.                                                         |
| `list(options=None, *, start=None, start_after=None, end=None, prefix=None, reverse=None, limit=None)` | A `dict` of key to value.                     |
| `get_alarm(options=None)`                                                               | The alarm time in milliseconds since the epoch, or `None`.      |
| `set_alarm(scheduled_time, options=None)`                                               | `None`. `scheduled_time` is a `datetime` or milliseconds since the epoch. |
| `delete_alarm(options=None)`                                                            | `None`.                                                         |

`sql` and `transaction` have no typed method. `self.ctx.storage.sql` is a
[`Binding`](#binding) over the JavaScript `SqlStorage`.

## HTTP

### `Request`

```python
Request(input, *, headers=None, body=None, method=None, redirect=None, cf=None)
```

`input` is a URL `str`, another `Request`, or a JavaScript `Request`. A
JavaScript `Request` with any keyword raises `ValueError`. `method` is a `str`
or an `http.HTTPMethod`. `headers` is a `dict`, a list of `(name, value)`
pairs, or a JavaScript `Headers`, and any other type raises `TypeError`.
`body` is a `str`, `bytes`, [`FormData`](#formdata), [`Blob`](#blob), a
JavaScript body, or `None`.

| Member                            | Type or return                                                   |
| --------------------------------- | ---------------------------------------------------------------- |
| `url`                             | `str`                                                            |
| `method`                          | `http.HTTPMethod`, or `str` for a non-standard method            |
| `headers`                         | A mapping, read with `request.headers["content-type"]` or `.get()` |
| `cf`                              | Cloudflare's request properties as a JavaScript object. `None` on a `Request` you build yourself. |
| `body`                            | The JavaScript `ReadableStream`                                  |
| `body_used`                       | `bool`                                                           |
| `async text()`                    | `str`                                                            |
| `async json(**kwargs)`            | The parsed body. `kwargs` go to `json.loads`.                    |
| `async bytes()`                   | `bytes`                                                          |
| `async buffer()`                  | The JavaScript `ArrayBuffer`                                     |
| `async form_data()`               | [`FormData`](#formdata)                                          |
| `async blob()`                    | [`Blob`](#blob)                                                  |
| `clone()`                         | A new `Request`                                                  |

`cache`, `credentials`, `destination`, `integrity`, `is_history_navigation`,
`keepalive`, `mode`, `redirect`, `referrer` and `referrer_policy` return the
JavaScript property of the same name.

Each body method, and `clone()`, raises `OSError("Body already used")` if the
body was read before.

### `Response`

```python
Response(body=None, status=None, status_text="", headers=None, web_socket=None)
```

`body` is a `str`, `bytes`, [`FormData`](#formdata), [`Blob`](#blob), a
JavaScript body such as a `ReadableStream`, a JavaScript `Response`, or `None`.
Any other type raises `TypeError`. A JavaScript `Response` with any other
argument raises `ValueError`. `status` is an `int` or `http.HTTPStatus`, and
defaults to `200`. `web_socket` is the client end of a `WebSocketPair`, for a
`101` response.

| Member                                                            | Description                                                         |
| ----------------------------------------------------------------- | ------------------------------------------------------------------- |
| `Response.json(data, status=200, status_text="", headers=None)`   | A response with `data` serialized by `json.dumps`. Sets `content-type: application/json` unless `headers` has a `content-type`. |
| `Response.redirect(url, status=302)`                              | A redirect response. A status other than 301, 302, 303, 307 or 308 raises `ValueError`. |
| `replace_body(body)`                                              | A new `Response` with this one's status and headers.                |
| `status`, `status_text`, `ok`, `url`, `headers`, `body`           | Properties of the response. `body` is `None` for a response with no body. |
| `async text()`, `async json()`, `async bytes()`                   | The body as `str`, parsed JSON or `bytes`.                          |
| `async form_data()`, `async blob()`                               | The body as [`FormData`](#formdata) or [`Blob`](#blob).             |
| `raise_for_status()`                                              | Raises `pyodide.http.HttpStatusError`, a subclass of `OSError`, for a 4xx or 5xx status. |

On the class, `Response.json(data)` builds a response. On an instance,
`await response.json()` parses its body. Reading a body a second time raises
`pyodide.http.BodyUsedError: Response body is already used`, a subclass of
`OSError`.

```python
from workers import Response

Response.json({"ok": True}, status=201, headers={"x-request-id": "42"})
```

`FetchResponse` is the base class of `Response`, with the same members.

### `fetch`

```python
async fetch(resource, *, method=None, headers=None, body=None, redirect=None, cf=None, fetcher=None) -> Response
```

Sends an HTTP request. `resource` is a URL `str`, a [`Request`](#request) or a
JavaScript `Request`. `cf` takes the
[`RequestInitCfProperties`](https://developers.cloudflare.com/workers/runtime-apis/request/#the-cf-property-requestinitcfproperties)
keys, such as `cacheTtl`. `fetcher` replaces the global `fetch` with another
JavaScript fetch function, such as a service binding's. A network failure
raises `pyodide.http.AbortError`, a subclass of `OSError`. An HTTP error status
doesn't raise.

### `Blob`

```python
Blob(blob_parts, content_type=None, endings=None)
```

`blob_parts` is one part or a list of parts. A part is `str`, `bytes`,
`memoryview`, `bytearray`, another `Blob` or a JavaScript buffer. `endings` is
`"transparent"`, `"native"` or the matching `BlobEnding` member.

| Member                                     | Description                       |
| ------------------------------------------ | --------------------------------- |
| `size`                                     | Size in bytes                     |
| `content_type`                             | The MIME type, or `""`            |
| `async text()`, `async bytes()`            | The contents                      |
| `slice(start=None, end=None, content_type=None)` | A new `Blob` of the byte range |

### `File`

```python
File(blob_parts, filename=None, content_type=None, endings=None, last_modified=None)
```

A `Blob` with a `name` and a `last_modified` time in milliseconds since the
epoch. It needs `filename`, unless `blob_parts` is a single `File`, whose
name and time it keeps. Without either, it raises `TypeError`.

### `FormData`

```python
FormData(form_data=None)
```

`form_data` is a `dict`, a JavaScript `FormData`, or `None` for an empty form.
Any other type raises `TypeError`. `FormData` is a mutable mapping:

| Member                                      | Description                                               |
| ------------------------------------------- | --------------------------------------------------------- |
| `form["key"]`                               | The first value for `key`. Raises `KeyError` if missing.  |
| `form["key"] = value`                       | Replaces every value for `key`. A list raises `TypeError`. |
| `append(key, value, filename=None)`         | Adds a value, keeping existing ones.                      |
| `get_all(key)`                              | Every value for `key`, as a `list`.                       |
| `delete(key)`, `del form["key"]`            | Removes every value for `key`.                            |
| `keys()`, `values()`, `items()`             | Generators over the entries.                              |
| `len(form)`                                 | The number of entries. A key with two values counts twice. |

A value is a `str`, a [`Blob`](#blob) or a [`File`](#file).

## Bindings

`workers.bindings` holds one class per binding type. `self.env` picks the
class from the binding's JavaScript type. A typed method takes snake_case
keywords and converts them to the camelCase JavaScript options. A keyword it
doesn't know goes into the options object unchanged, and a method uses a
dictionary in the `options` position as the options object.

### `Binding`

The base class of every binding class, and the class of any binding without
its own, such as `ai` or `browser`. Every attribute forwards to the JavaScript
object. Calls convert their arguments with
[`python_to_rpc`](#rpc-conversion) and results with
[`python_from_rpc`](#rpc-conversion), and a JavaScript promise becomes a
coroutine. `Binding(js_object)` wraps a JavaScript object, and
`Binding(binding)` returns `binding` unchanged.

### `KVNamespace`

| Method                                                                          | Returns                                                           |
| ------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `async get(key, type="text", *, cache_ttl=None)`                                | `str` for `"text"`, the parsed value for `"json"`, `memoryview` for `"arrayBuffer"`, a stream for `"stream"`. `None` if the key is missing. |
| `async get_with_metadata(key, type="text", *, cache_ttl=None)`                  | `{"value": ..., "metadata": ..., "cacheStatus": ...}`. For a missing key, all three are `None`. |
| `async put(key, value, options=None, *, expiration=None, expiration_ttl=None, metadata=None)` | `None`. `expiration` is seconds since the epoch, `expiration_ttl` is seconds from now, at least 60. A smaller `expiration_ttl` raises `JsException` with `KV PUT failed: 400 Invalid expiration_ttl`. |
| `async delete(key)`                                                             | `None`                                                            |
| `async list(options=None, *, prefix=None, limit=None, cursor=None)`             | `{"keys": [...], "list_complete": bool, "cacheStatus": ...}`. Each key is a `dict` with `name`, plus `expiration` and `metadata` when set. When `list_complete` is `False`, `cursor` holds the value to pass for the next page. |

### `D1Database`

| Method                       | Returns                                                       |
| ---------------------------- | ------------------------------------------------------------- |
| `prepare(query)`             | A [`D1PreparedStatement`](#d1preparedstatement)               |
| `async batch(statements)`    | A `list` of result `dict`s, one per statement, run in one transaction |
| `async exec(query)`          | A `dict` with `count` and `duration`. Runs one or more statements without bound values. |

### `D1PreparedStatement`

| Method                                       | Returns                                                     |
| -------------------------------------------- | ----------------------------------------------------------- |
| `bind(*values)`                              | A new `D1PreparedStatement` with `?` placeholders filled     |
| `async first(column=None)`                   | The first row as a `dict`, or one column's value. `None` when the query returns no rows. |
| `async all()`                                | `{"results": [...], "success": bool, "meta": {...}}`        |
| `async run()`                                | The same `dict` as `all()`                                  |
| `async raw(options=None, *, column_names=None)` | Rows as lists. With `column_names=True`, the first list is the column names. |

### `R2Bucket`

| Method                                                                        | Returns                                                         |
| ----------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `async head(key)`                                                             | An [`R2Object`](#r2object), or `None`                           |
| `async get(key, options=None)`                                                | An [`R2ObjectBody`](#r2objectbody), an `R2Object` when an `onlyIf` condition fails, or `None` |
| `async put(key, value, options=None, *, http_metadata=None, custom_metadata=None)` | An `R2Object`, or `None` when an `onlyIf` condition fails     |
| `async delete(keys)`                                                          | `None`. `keys` is one key or a list of up to 1,000.             |
| `async list(options=None, *, prefix=None, limit=None, cursor=None, delimiter=None)` | A `dict` with `objects`, a list of `R2Object`, plus `truncated`, `cursor` and `delimitedPrefixes`. When `truncated` is `True`, pass `cursor` to get the next page. |

`http_metadata` and `custom_metadata` keep their keys as written, so use R2's
camelCase names such as `contentType`.

### `R2Object`

| Property          | Type                |
| ----------------- | ------------------- |
| `key`             | `str`               |
| `version`         | `str`               |
| `size`            | `int`, in bytes     |
| `etag`            | `str`               |
| `http_etag`       | `str`               |
| `uploaded`        | `datetime`          |
| `http_metadata`   | `dict`              |
| `custom_metadata` | `dict[str, str]`    |

### `R2ObjectBody`

An `R2Object` with a body. It adds `body`, the JavaScript `ReadableStream`,
plus `body_used`, `async text()`, `async bytes()` and `async json()`.

### `Queue`

| Method                                                                      | Returns |
| --------------------------------------------------------------------------- | ------- |
| `async send(body, options=None, *, content_type=None, delay_seconds=None)`  | `None`  |
| `async send_batch(messages, options=None, *, delay_seconds=None)`           | `None`. Each message is a `dict` with a `"body"` key. |

### `DurableObjectNamespace`

`DurableObjectNamespace[T]` returns stubs typed as `T`, your Durable Object
class.

| Method                                     | Returns                                                     |
| ------------------------------------------ | ----------------------------------------------------------- |
| `get_by_name(name, options=None)`          | A stub for the object named `name`                          |
| `id_from_name(name)`                       | A JavaScript `DurableObjectId`                              |
| `id_from_string(id)`                       | A JavaScript `DurableObjectId`                              |
| `new_unique_id(options=None)`              | A new JavaScript `DurableObjectId`                          |
| `get(id, options=None)`                    | A stub for the object with `id`                             |
| `jurisdiction(name)`                       | A `DurableObjectNamespace` limited to that jurisdiction     |

A stub is a [`Fetcher`](#fetcher). Call your class's methods on it with
positional arguments only. Forwarded names on the namespace, such as
`idFromName`, return raw JavaScript values.

### `Fetcher`

A service binding, the assets binding, or a Durable Object stub.

| Method                          | Returns                                                |
| ------------------------------- | ------------------------------------------------------ |
| `async fetch(input, **options)` | A [`Response`](#response). `options` are as for [`fetch`](#fetch). |

Any other method is an RPC call. Pass arguments by position, because a
keyword argument reaches the other side as one trailing dictionary.

### `Workflow`

| Method                                              | Returns                                              |
| --------------------------------------------------- | ---------------------------------------------------- |
| `async create(options=None, *, id=None, params=None)` | A [`WorkflowInstance`](#workflowinstance)          |
| `async create_batch(batch)`                         | A `list` of `WorkflowInstance`. Each item is a `dict` with `id` and `params`. |
| `async get(id)`                                     | A `WorkflowInstance`                                 |

### `WorkflowInstance`

| Member                                                  | Returns                                                           |
| ------------------------------------------------------- | ----------------------------------------------------------------- |
| `id`                                                    | `str`                                                             |
| `async status()`                                        | A `dict` with `status`, `output` and `error`. [Write a Workflow](workflows.md#instance-status) lists the `status` values. `output` is `None` until the instance completes. `error` is `None` unless it errored, and then a `dict` with `name` and `message`. |
| `async pause()`, `resume()`, `restart()`, `terminate()` | `None`                                                            |
| `async send_event(event=None, *, type=None, payload=None)` | `None`                                                         |

### Other binding classes

These classes have typed methods with snake_case keywords, like the classes
above. Each returns Python values.

| Class                    | Config section              | Methods                                                                                   |
| ------------------------ | --------------------------- | ----------------------------------------------------------------------------------------- |
| `Vectorize`              | `vectorize`                 | `async describe()`, `async query(vector, *, top_k, namespace, return_values, return_metadata, filter)`, `async query_by_id(vector_id, ...)` with the same keywords, `async insert(vectors)`, `async upsert(vectors)`, `async delete_by_ids(ids)`, `async get_by_ids(ids)`. `ids` is one id or a list. |
| `AnalyticsEngineDataset` | `analytics_engine_datasets` | `write_data_point(event=None, *, indexes, doubles, blobs)`, not async. `indexes` and `blobs` must be lists, and a bare `str` or `bytes` raises `TypeError`. |
| `ImagesBinding`          | `images`                    | `async info(stream)`, and `input(stream)`, which returns an `ImageTransformer`. Chain `transform(**options)` and `draw(image, ...)`, then `await output(format=..., quality=...)`. The result has `response()`, `content_type()` and `image()`. |
| `RateLimit`              | `ratelimits`                | `async limit(*, key)`, which returns the JavaScript result as a `dict`.                  |

Every keyword on these methods defaults to `None`, and a `None` keyword is
left out of the JavaScript options.

## Workflows

`workers.workflows` has the step API that `WorkflowEntrypoint.run` receives.

### `WorkflowStep`

The `step` argument of `run`.

| Method                                                                 | Description                                                    |
| ---------------------------------------------------------------------- | -------------------------------------------------------------- |
| `do(name=None, depends=None, concurrent=False, config=None)`          | A decorator that makes a function a step. Returns an async callable that runs it. `name` defaults to the function's name. |
| `async sleep(name, duration)`                                          | Pauses the Workflow. `duration` is a string such as `"1 hour"` or milliseconds. |
| `async sleep_until(name, timestamp)`                                   | Pauses until `timestamp`, a `datetime` or milliseconds since the epoch.   |
| `async wait_for_event(name, event_type, timeout="24 hours")`           | Waits for an event sent with `WorkflowInstance.send_event`. Returns a `dict` with `type`, `timestamp` and `payload`. `timeout` defaults to 24 hours. |

`do` raises `TypeError` when:

- You pass `depends` while `python_workflows_implicit_dependencies` is on.
  workerd turns the flag on for compatibility dates from 2026-02-25.
- With the flag on, a parameter other than `ctx` names no earlier step.
- With the flag off, the number of parameters other than `ctx` differs from
  the length of `depends`, or an item in `depends` isn't a step.

`config` is the JavaScript step config as a `dict`, for example
`{"retries": {"limit": 3, "delay": "5 seconds", "backoff": "exponential"}, "timeout": "1 minute"}`.

### `NonRetryableError`

Raise it inside a step to fail the step without retries. Awaiting the step in
`run` raises `NonRetryableError` with the same message. The runtime retries
any other exception a step raises, as `config` allows. Once the retries run out, awaiting
the step raises that exception's class for `TypeError` and `ValueError`, and
`pyodide.ffi.JsException` for other classes.

## Module functions

| Name                              | Description                                                                                       |
| --------------------------------- | ------------------------------------------------------------------------------------------------- |
| `env`                             | The Worker's env, read on access. Use it outside a handler, such as in module-level helpers.      |
| `wait_until(awaitable)`           | The runtime's module-level `waitUntil`. Under `wynd dev` it doesn't keep the Worker alive after the response. Prefer `self.ctx.wait_until`. |
| `waitUntil`                       | Alias of `wait_until`.                                                                             |
| `import_from_javascript(name)`    | Imports a JavaScript module. `"cloudflare:workers"` and `"cloudflare:sockets"` work on every target. Others raise `ImportError` on Python 3.12. |
| `patch_env(...)`                  | Raises `NotImplementedError`. workerd removed the runtime helper it needed on 2026-06-30.             |

## RPC conversion

`workers.rpc` has the conversions the runtime applies to RPC arguments and
results.

`python_to_rpc(value)` converts a Python value for JavaScript:

| Python                                   | JavaScript                         |
| ---------------------------------------- | ---------------------------------- |
| `None`                                   | `null`                             |
| `dict`                                   | Plain object                       |
| `list`                                   | Array                              |
| `set`                                    | `Set`                              |
| `bytes`                                  | `Uint8Array`                       |
| `datetime`                               | `Date`                             |
| `Exception`                              | `Error` with `str(exception)`      |
| A wrapper with `js_object`               | Its `js_object`                    |
| A function                               | A callable proxy                   |
| `tuple`, `bytearray`, an awaitable, a JavaScript `RegExp` | Raises `TypeError`, at any depth |
| An instance of your own class, `complex`, `range`, a generator | Converts, then the RPC send raises `JsException` with `DataCloneError` |

`python_from_rpc(value)` converts the other way. A plain object becomes a
`JsDict`, a `dict` whose keys also read as attributes, so `result.meta` and
`result["meta"]` are the same. `null` and `undefined` become `None`, a `Date`
becomes a `datetime`, a `Uint8Array` becomes a `memoryview`, an `Error`
becomes an `Exception`, and a JavaScript `Request`, `Response`, `FormData`,
`Blob` or `File` becomes the crosswind class.

## Adapters

### `workers.asgi`

| Name                                         | Description                                                                           |
| -------------------------------------------- | ------------------------------------------------------------------------------------- |
| `async fetch(app, req, env, ctx=None)`       | Runs the ASGI `app` on `req` and returns a JavaScript `Response`. The scope has the env under `"env"`. |
| `async websocket(app, req, env=None, ctx=None)` | Runs a WebSocket connection through `app` and returns the `101` response.          |
| `entrypoint(app)`                            | A `WorkerEntrypoint` subclass that calls `websocket` for upgrade requests and `fetch` otherwise, passing `self.ctx` to both. |
| `env`                                        | A FastAPI dependency that returns the Worker's env. Reading it without FastAPI installed raises `ImportError`. |

`fetch` runs the app's lifespan startup before each request and its shutdown
after the response. It reads the whole request body before the app runs. The
scope has no `client`, `server` or `root_path` keys.

`import asgi` imports the same module, as in the stock SDK.

### `workers.wsgi`

| Name                                         | Description                                                                           |
| -------------------------------------------- | ------------------------------------------------------------------------------------- |
| `async fetch(app, req, env, ctx=None)`       | Runs the WSGI `app` on `req` and returns a JavaScript `Response`. The environ has the env under `"workers.env"`. |
| `entrypoint(app)`                            | A `WorkerEntrypoint` subclass that calls `fetch`. It has no WebSocket support.        |

The app runs synchronously inside the request. On Python 3.13 and later,
`wsgi.input` streams the body as the app reads it. Python 3.12 has no
`run_sync`, so there the adapter reads the whole body first.
