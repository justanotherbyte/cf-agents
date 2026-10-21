"""Runs durable_tests.py inside workerd against a real Durable Object."""

import asyncio
import json
import time
import traceback

import durable_tests
import js

from workers import DurableObject, Response, WorkerEntrypoint


class Room(DurableObject):
    def __init__(self, ctx, env):
        self.types_before_super_init = [type(ctx).__name__, type(env.KV).__name__]
        super().__init__(ctx, env)
        self.calls = 0

    async def init_types(self):
        return self.types_before_super_init

    async def bump(self):
        self.calls += 1
        return self.calls

    async def explode(self):
        self.ctx.abort("bye")

    async def identity(self):
        return str(self.ctx.id)

    async def blocked(self):
        async def work():
            return 5

        return await self.ctx.block_concurrency_while(work)

    async def storage_roundtrip(self):
        storage = self.ctx.storage
        await storage.put("a", {"n": [1, None]})
        await storage.put({"b": 2, "c": 3})
        assert await storage.get("a") == {"n": [1, None]}
        assert await storage.get(["b", "c", "missing"]) == {"b": 2, "c": 3}
        assert await storage.get("missing") is None
        assert await storage.list(prefix="b") == {"b": 2}
        assert await storage.delete("b") is True
        assert await storage.delete(["c", "nope"]) == 1
        await storage.deleteAll()
        assert await storage.list() == {}
        rows = storage.sql.exec("SELECT 1 AS one").toArray()
        assert rows == [{"one": 1}], rows
        return "ok"

    async def defer(self):
        async def later():
            await asyncio.sleep(0.05)
            await self.ctx.storage.put("deferred", True)

        # workerd's patchWaitUntil skips a DurableObjectState, so this needs
        # crosswind's own wait_until to keep the coroutine alive.
        self.ctx.wait_until(later())

    async def deferred(self):
        return await self.ctx.storage.get("deferred")

    async def schedule_alarm(self):
        await self.ctx.storage.set_alarm(int(time.time() * 1000) + 50)

    async def alarm(self, info=None):
        # workerd's alarm info is a class instance, so it arrives as a Binding.
        retry = None if info is None else info.retryCount
        await self.ctx.storage.put(
            "alarm", {"type": type(info).__name__, "retry": retry}
        )

    async def alarm_seen(self):
        return await self.ctx.storage.get("alarm")

    async def fetch(self, request):
        client, server = js.Object.values(js.WebSocketPair.new())
        self.ctx.accept_web_socket(server)
        return Response(None, status=101, web_socket=client)

    async def webSocketMessage(self, ws, message):  # noqa: N802
        sockets = len(self.ctx.get_web_sockets())
        ws.send(f"{message} {type(ws).__name__} {sockets}")


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        names = [name for name in dir(durable_tests) if name.startswith("test_")]
        failures = {}
        for name in names:
            try:
                await getattr(durable_tests, name)(self)
            except BaseException:  # noqa: BLE001
                failures[name] = traceback.format_exc()
        return Response(json.dumps({"ran": names, "failures": failures}))
