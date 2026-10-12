"""The spec's API shape, checked by `uvx ty check`: assert_type fails the type
check if a binding's type stops flowing through. At runtime assert_type does
nothing, so pytest only checks that the classes define."""

from typing import Protocol, assert_type

from workers import DurableObject, Request, Response, WorkerEntrypoint
from workers.bindings import DurableObjectNamespace, KVNamespace


class Env(Protocol):
    COUNTER: DurableObjectNamespace["Counter"]
    CACHE: KVNamespace


class Counter(DurableObject[Env]):
    async def increment(self, by: int) -> int:
        return by


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request: Request) -> Response:
        assert_type(self.env, Env)
        counter = self.env.COUNTER.get_by_name("global")
        assert_type(counter, Counter)
        value = await counter.increment(2)
        assert_type(value, int)
        await self.env.CACHE.put("last", str(value), expiration_ttl=60)
        assert_type(await self.env.CACHE.get("last"), str | None)
        return Response.json({"value": value})


def test_typed_classes_define() -> None:
    assert issubclass(Default, WorkerEntrypoint)
