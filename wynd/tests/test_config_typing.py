"""The types an Env class gives, checked by `uvx ty check`: assert_type fails
the type check if a binding's runtime type stops flowing through its builder.
At runtime assert_type does nothing, so pytest only checks that Env defines."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict, assert_type

from wynd.config import bindings, context

# Named only in assert_type's strings, because workers imports js at runtime.
if TYPE_CHECKING:
    from workers.bindings import (  # noqa: F401
        D1Database,
        DurableObjectNamespace,
        KVNamespace,
        Queue,
    )


class Job(TypedDict):
    id: str


class Counter:
    async def increment(self, by: int) -> int:
        return by


class Env:
    ORIGIN = bindings.text("https://api.example.com")
    FLAGS = bindings.json({"search": True})
    TOKEN = bindings.secret()
    DB = bindings.d1(name="db")
    JOBS = bindings.queue(Job, name="jobs")
    CACHE = bindings.kv() if context().mode == "staging" else None
    COUNTER = bindings.DurableObject["Counter"](export_name="Counter")


def check(env: Env) -> None:
    assert_type(env.ORIGIN, str)
    assert_type(env.FLAGS, dict[str, bool])
    assert_type(env.TOKEN, str)
    assert_type(env.DB, "D1Database")
    assert_type(env.JOBS, "Queue[Job]")
    assert_type(env.CACHE, "KVNamespace | None")
    assert_type(env.COUNTER, "DurableObjectNamespace[Counter]")
    assert_type(Env.DB, "bindings.Spec[D1Database]")


def test_env_defines() -> None:
    assert Env.DB.section == "d1_databases"
