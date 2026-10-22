---
title: "Troubleshoot crosswind-workers errors"
description: "Causes and fixes for crosswind-workers errors: the stock SDK conflict, a missing env module, RPC argument errors, unknown binding methods, queue handler arguments, threads and JavaScript imports."
---

Each heading is the text you see in `wynd dev` output or in a traceback.
Search this page for the last line of your error.

## `ImportError: workers-runtime-sdk is installed beside crosswind-workers`

**Cause.** Both SDKs are in `python_modules/`. They both install the
`workers` package, so one overwrote the other's files. pywrangler causes this
because it always adds `workers-runtime-sdk`. A project that declares both
causes it too.

**Fix.**

1. Build with Wynd, not pywrangler. See
   [Switch from pywrangler](../wynd/getting-started.md).
2. Keep one SDK in `[project] dependencies` in `pyproject.toml`.
3. Run `uvx wynd dev`. Wynd reinstalls `python_modules/` and leaves out the
   SDK you didn't declare, even when another package depends on it.

## `ModuleNotFoundError: No module named 'env'`

**Cause.** `src/env.py` doesn't exist. Wynd writes it when you run
`wynd dev`, `wynd deploy` or `wynd types`. Running `wrangler dev` or
pywrangler directly skips that.

**Fix.** Run `uvx wynd types`, and commit `src/env.py`.

## `TypeError: Room.post() missing 1 required positional argument`

**Cause.** You called an RPC method with keyword arguments, as in
`await room.post(author="ada", text="hello")`. JavaScript RPC has no keyword
arguments, so the keywords arrive as one dictionary in the first parameter.
The caller sees `pyodide.ffi.JsException` wrapping the `TypeError`. When the
method has one parameter, nothing is missing, and the error comes later from
the dictionary, such as `unsupported operand type(s) for +: 'int' and
'JsDict'`.

**Fix.** Pass the arguments by position:

```python
# bad
await room.post(author="ada", text="hello")

# good
await room.post("ada", "hello")
```

This applies to Durable Object stubs and service binding RPC. Binding methods
such as `KVNamespace.put` take keywords.

## `TypeError: <class 'tuple'> cannot be sent over RPC.`

**Cause.** An RPC argument or return value contains a `tuple` or a
`bytearray`, which have no JavaScript equivalent. An instance of your own class
fails for the same reason with `JsException: DataCloneError: Proxy could not
be serialized because it is not a valid RPC receiver type.`

**Fix.** Convert the value before sending it: a `tuple` to a `list`, a
`bytearray` to `bytes`, an object to a `dict`.

## `AttributeError` with a snake_case method name on a binding

**Cause.** The binding class has no typed method with that name, so crosswind
forwarded it to the JavaScript object, which doesn't have it either. crosswind
doesn't convert names from snake_case to camelCase.

**Fix.** Check the class in the [API reference](api.md#bindings). If the
method isn't there, call it by its JavaScript name, such as
`getWithMetadata`.

## `OSError: Body already used`

**Cause.** You read a request body twice, for example `text()` after
`json()`. A response body read twice raises
`pyodide.http.BodyUsedError: Response body is already used`, also an
`OSError`.

**Fix.** Read it once and keep the result. To read it twice, call `clone()`
before the first read.

## `TypeError: Default.queue() takes 2 positional arguments but 4 were given`

**Cause.** The runtime calls a queue consumer as `queue(batch, env, ctx)`, and
your method takes only `batch`. Every batch fails, and the runtime delivers it
again.

**Fix.** Accept all three, and keep using `self.env` and `self.ctx`, because
the `env` and `ctx` arguments arrive as `None`:

```python
async def queue(self, batch, env, ctx):
    for message in batch.messages:
        message.ack()
```

`scheduled` takes the same three arguments: `scheduled(self, controller, env, ctx)`.

## `RuntimeError: can't start new thread`

**Cause.** A Worker has one thread. crosswind-workers runs
`anyio.to_thread.run_sync` inline, which covers FastAPI's sync routes.
Other code that starts a thread, such as `concurrent.futures.ThreadPoolExecutor`
or `threading.Thread`, still fails.

**Fix.** Call the function directly, or make it `async`.

## `ImportError: Failed to import 'cloudflare:email': Only 'cloudflare:workers' and 'cloudflare:sockets' are available until the next python runtime version.`

**Cause.** `import_from_javascript` can import other modules only on Python
3.13 and later. Your compatibility date selects 3.12.

**Fix.** Move the compatibility date to 2025-09-29 or later. See
[Python targets](../wynd/commands.md#python-targets).

## `TypeError: depends is not accepted with python_workflows_implicit_dependencies`

**Cause.** The flag is on for your compatibility date, so steps find their
dependencies by parameter name.

**Fix.** Remove `depends=` and name each parameter after the step whose
result it takes. See [Steps](workflows.md#steps).

## `NotImplementedError: patch_env needs a workerd helper that workerd removed`

**Cause.** `patch_env` relied on a runtime helper that workerd removed on
2026-06-30. It fails on the stock SDK too, with `AttributeError`.

**Fix.** Pass the values you need as arguments.

## `Worker tried to return a WebSocket in a response to a request which did not contain the header "Upgrade: websocket"`

**Cause.** A handler returned `Response(None, status=101, web_socket=client)`
for a plain HTTP request, such as a browser visit or a health check.

**Fix.** Check the header before creating the `WebSocketPair`:

```python
if (request.headers.get("upgrade") or "").lower() != "websocket":
    return Response("expected a WebSocket", status=426)
```

## `Uncaught Error: This borrowed proxy was automatically destroyed at the end of a function call.`

**Cause.** A coroutine or Python function went to a JavaScript method through
its camelCase name, such as `self.ctx.waitUntil(coro)` in a Durable Object.
JavaScript kept a reference to it after the call returned, and Pyodide had
already freed it. The call itself returns without an error. The message
appears only in the `wynd dev` log, and the coroutine never runs, so the log
also shows `RuntimeWarning: coroutine ... was never awaited`.

**Fix.** Use the typed method, which keeps the coroutine alive until it
finishes:

```python
# bad
self.ctx.waitUntil(self.ctx.storage.put("x", 1))

# good
self.ctx.wait_until(self.ctx.storage.put("x", 1))
```

## Background work stops after the response

**Cause.** The runtime can stop the Worker once it sends the response. Under
`wynd dev`, the runtime doesn't track a task started with
`asyncio.create_task` or handed to the module-level `wait_until`.

**Fix.** Pass the coroutine to `self.ctx.wait_until`:

```python
self.ctx.wait_until(self.env.CACHE.put("last-seen", now))
```
