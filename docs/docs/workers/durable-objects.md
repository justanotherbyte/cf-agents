---
title: "Build a Durable Object in Python"
description: "Define a Durable Object with crosswind-workers, store data with typed storage methods or SQL, schedule alarms, accept WebSockets and call it over RPC."
---

A [Durable Object](https://developers.cloudflare.com/durable-objects/) is a
class with its own storage. Each name you look up maps to one instance, and
requests to that name reach the same instance. This page builds a chat room
that stores messages, sends them to WebSocket clients and cleans up with an
alarm.

Every example uses this config:

```jsonc title="wrangler.jsonc"
{
  "name": "rooms",
  "main": "src/entry.py",
  "compatibility_date": "2026-09-08",
  "compatibility_flags": ["python_workers"],
  "durable_objects": {
    "bindings": [{ "name": "ROOM", "class_name": "Room" }]
  },
  "migrations": [{ "tag": "v1", "new_sqlite_classes": ["Room"] }]
}
```

## Define the class

Subclass [`DurableObject`](api.md#entrypoints) and give it `async` methods.
Workers call those methods over RPC through a stub:

```python title="src/entry.py"
from env import Env
from workers import DurableObject, Request, Response, WorkerEntrypoint


class Room(DurableObject[Env]):
    async def post(self, author: str, text: str) -> int:
        messages = await self.ctx.storage.get("messages") or []
        messages.append({"author": author, "text": text})
        await self.ctx.storage.put("messages", messages)
        return len(messages)

    async def history(self) -> list[dict]:
        return await self.ctx.storage.get("messages") or []


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        room = self.env.ROOM.get_by_name("lobby")
        await room.post("ada", "hello")
        return Response.json(await room.history())
```

On the first request:

```json
[{"author": "ada", "text": "hello"}]
```

`self.env.ROOM.get_by_name("lobby")` returns a stub that your type checker
treats as a `Room`, so it checks method names and argument types.

> **Warning**
>
> Pass RPC arguments by position. JavaScript RPC has no keyword arguments.
> `room.post(author="ada", text="hello")` reaches `post` as one dictionary in
> `author` and nothing in `text`, so the call fails with
> `TypeError: Room.post() missing 1 required positional argument: 'text'`.

Arguments and return values cross RPC as copies. `dict`, `list`, `str`,
`bytes`, numbers, `None`, `datetime` and crosswind's `Request` and `Response`
all convert both ways. A `tuple` or a `bytearray` raises
`TypeError: <class 'tuple'> cannot be sent over RPC.`, and an instance of your
own class raises a `DataCloneError`. Convert the value to a `list`, `bytes` or
`dict` first.

## Store data

`self.ctx.storage` is a [`DurableObjectStorage`](api.md#durableobjectstorage).
Its key-value methods take and return Python values:

```python
await self.ctx.storage.put("count", 3)
await self.ctx.storage.put({"a": 1, "b": 2})  # several keys at once
values = await self.ctx.storage.get(["a", "b"])  # {"a": 1, "b": 2}
deleted = await self.ctx.storage.delete("a")  # True if the key existed
listing = await self.ctx.storage.list(prefix="b")
await self.ctx.storage.delete_all()
```

For SQL, use the JavaScript `sql` API. It has no typed wrapper, so call it
by its JavaScript names:

```python title="src/entry.py"
from env import Env
from workers import DurableObject, Request, Response, WorkerEntrypoint


class Room(DurableObject[Env]):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.ctx.storage.sql.exec(
            "CREATE TABLE IF NOT EXISTS messages (author TEXT, text TEXT)"
        )

    async def post(self, author: str, text: str) -> None:
        self.ctx.storage.sql.exec("INSERT INTO messages VALUES (?, ?)", author, text)

    async def history(self) -> list[dict]:
        return self.ctx.storage.sql.exec("SELECT author, text FROM messages").toArray()


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        room = self.env.ROOM.get_by_name("lobby")
        await room.post("ada", "hello")
        return Response.json(await room.history())
```

`toArray()` returns a list of dictionaries, one per row. SQL storage needs the
class in `new_sqlite_classes` in your migrations, as in the config above.

## Schedule an alarm

An alarm runs the object's `alarm` method at a set time, once. Pass a
`datetime` or milliseconds since the epoch:

```python title="src/entry.py"
from datetime import UTC, datetime, timedelta

from env import Env
from workers import DurableObject, Request, Response, WorkerEntrypoint


class Room(DurableObject[Env]):
    async def post(self, author: str, text: str) -> None:
        await self.ctx.storage.put("last", text)
        # Clear the room an hour after the last message.
        await self.ctx.storage.set_alarm(datetime.now(UTC) + timedelta(hours=1))

    async def alarm(self, alarm_info=None):  # the runtime passes retry details
        await self.ctx.storage.delete_all()


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        room = self.env.ROOM.get_by_name("lobby")
        await room.post("ada", "hello")
        return Response("alarm set")
```

Setting an alarm replaces the previous one. `get_alarm()` returns the
scheduled time in milliseconds, or `None`. `delete_alarm()` cancels it.

## Accept WebSockets

Accept the server end of a `WebSocketPair` with `accept_web_socket`, and
return the client end in a `101` response. The runtime then calls
`webSocketMessage`, `webSocketClose` and `webSocketError` on the object. With
this API the object can leave memory between messages, and the connection
stays open.

```python title="src/entry.py"
import js

from env import Env
from workers import DurableObject, Request, Response, WorkerEntrypoint


class Room(DurableObject[Env]):
    async def fetch(self, request: Request) -> Response:
        client, server = js.WebSocketPair.new().object_values()
        self.ctx.accept_web_socket(server)
        return Response(None, status=101, web_socket=client)

    async def webSocketMessage(self, ws, message):
        for socket in self.ctx.get_web_sockets():
            socket.send(f"echo: {message}")

    async def webSocketClose(self, ws, code, reason, was_clean):
        ws.close(code, reason)


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        return await self.env.ROOM.get_by_name("lobby").fetch(request)
```

Connect with any WebSocket client, send `hi`, and every client in the room
receives `echo: hi`.

Handler names stay camelCase, because the runtime calls them by those names.
`ws` is a [`Binding`](api.md#binding), so `ws.send()` and `ws.close()` call the
JavaScript `WebSocket` methods. A text message arrives as `str`. A binary
message arrives as a JavaScript `ArrayBuffer`, so the example above echoes it
as `echo: [object ArrayBuffer]`. Call `message.to_bytes()` to read its
bytes.

## Run code before the first request

`__init__` can't await, so async setup such as reading storage needs
`blockConcurrencyWhile`. It holds back every request until the coroutine
finishes:

```python
class Room(DurableObject[Env]):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.ctx.blockConcurrencyWhile(self.load)

    async def load(self):
        self.messages = await self.ctx.storage.get("messages") or []
```

Use the JavaScript name here. The typed `block_concurrency_while` is `async`,
so calling it from `__init__` without `await` never runs it.

## Reset the object

`self.ctx.abort()` resets the object and stops the current call by raising
[`DurableObjectAbort`](api.md#durableobjectcontext). It derives from
`BaseException`, so `except Exception` doesn't catch it.

## Older compatibility dates

Before 2025-08-14 the runtime calls `on_fetch`, `on_connect`, `on_alarm` and
`on_webSocketMessage` in place of the plain names. crosswind adds those names
for you, so write `fetch`, `alarm` and `webSocketMessage` on any date.
