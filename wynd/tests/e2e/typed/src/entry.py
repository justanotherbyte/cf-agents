from typing import assert_type

from env import Env
from workers import DurableObject, Response, WorkerEntrypoint


class Counter(DurableObject[Env]):
    async def increment(self, by: int) -> int:
        return by + 1


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request):
        counter = self.env.COUNTER.get_by_name("one")
        assert_type(counter, Counter)
        value = await counter.increment(1)
        await self.env.CACHE.put("greeting", self.env.GREETING)
        return Response(f"{value} {await self.env.CACHE.get('greeting')}")
