"""Each test gets the Default entrypoint's env, wrapped by crosswind."""

import asyncio

from workers import Response
from workers.bindings import (
    Binding,
    D1Database,
    DurableObjectNamespace,
    Fetcher,
    KVNamespace,
    Queue,
    R2Bucket,
    R2ObjectBody,
    Workflow,
)


async def test_env_classes(env):
    assert isinstance(env.KV, KVNamespace)
    assert isinstance(env.DB, D1Database)
    assert isinstance(env.BUCKET, R2Bucket)
    assert isinstance(env.QUEUE, Queue)
    assert isinstance(env.COUNTER, DurableObjectNamespace)
    assert isinstance(env.OTHER, Fetcher)
    assert isinstance(env.FLOW, Workflow)
    assert env.TEXT == "hello"
    assert env.FLAGS == {"beta": True}
    assert env.FLAGS["beta"] is True


async def test_kv(env):
    await env.KV.put("text", "value", expiration_ttl=3600)
    await env.KV.put("json", '{"n": [1, 2]}', metadata={"m": 1})
    assert await env.KV.get("text") == "value"
    assert await env.KV.get("json", "json") == {"n": [1, 2]}
    assert await env.KV.get("text", "arrayBuffer") == b"value"
    assert await env.KV.get("missing") is None
    with_metadata = await env.KV.get_with_metadata("json")
    assert with_metadata["metadata"] == {"m": 1}
    listing = await env.KV.list(prefix="te")
    assert [key["name"] for key in listing["keys"]] == ["text"]
    # Stock style: camelCase passthrough and options as JS keywords.
    await env.KV.put("stock", "v", expirationTtl=3600)
    stock = await env.KV.getWithMetadata("stock")
    assert stock["value"] == "v"
    await env.KV.delete("text")
    assert await env.KV.get("text") is None


async def test_d1(env):
    await env.DB.exec("CREATE TABLE IF NOT EXISTS t (id INTEGER, name TEXT)")
    insert = env.DB.prepare("INSERT INTO t VALUES (?, ?)")
    await env.DB.batch([insert.bind(1, "one"), insert.bind(2, "two")])
    rows = await env.DB.prepare("SELECT * FROM t ORDER BY id").all()
    assert rows["results"] == [{"id": 1, "name": "one"}, {"id": 2, "name": "two"}]
    first = env.DB.prepare("SELECT name FROM t WHERE id = ?").bind(2)
    assert await first.first("name") == "two"
    raw = await env.DB.prepare("SELECT id FROM t").raw(column_names=True)
    assert raw == [["id"], [1], [2]]
    run = await env.DB.prepare("DELETE FROM t").run()
    assert run["success"] is True
    assert run["meta"]["changes"] == 2


async def test_r2(env):
    put = await env.BUCKET.put(
        "a.txt",
        "contents",
        custom_metadata={"k": "v"},
        http_metadata={"contentType": "text/plain"},
    )
    assert put.key == "a.txt"
    assert put.size == 8
    got = await env.BUCKET.get("a.txt")
    assert isinstance(got, R2ObjectBody)
    assert await got.text() == "contents"
    assert got.custom_metadata == {"k": "v"}
    assert got.http_metadata["contentType"] == "text/plain"
    head = await env.BUCKET.head("a.txt")
    assert head.etag == put.etag
    await env.BUCKET.put("b.bin", b"\x00\x01")
    assert await (await env.BUCKET.get("b.bin")).bytes() == b"\x00\x01"
    listing = await env.BUCKET.list(prefix="a")
    assert [item.key for item in listing["objects"]] == ["a.txt"]
    await env.BUCKET.delete(["a.txt", "b.bin"])
    assert await env.BUCKET.get("a.txt") is None


async def test_queue(env):
    await env.QUEUE.send({"n": 1}, content_type="json")
    await env.QUEUE.send("text", contentType="text")
    await env.QUEUE.send_batch([{"body": 1}, {"body": 2}])


async def test_durable_object_rpc(env):
    counter = env.COUNTER.get_by_name("counter")
    assert await counter.increment(2) == 2
    assert await counter.increment(3) == 5
    by_id = env.COUNTER.get(env.COUNTER.id_from_name("counter"))
    assert await by_id.increment(1) == 6


async def test_service_fetch_and_rpc(env):
    response = await env.OTHER.fetch("https://other/")
    assert isinstance(response, Response)
    assert await response.text() == "from other"
    assert await env.OTHER.add(2, 3) == 5


async def test_workflow(env):
    instance = await env.FLOW.create(params={"p": 1})
    for _ in range(50):
        status = await instance.status()
        if status["status"] == "complete":
            break
        await asyncio.sleep(0.1)
    assert status["status"] == "complete", status
    assert status["output"] == "done"
    again = await env.FLOW.get(instance.id)
    assert again.id == instance.id


async def test_unknown_binding_passes_through(env):
    # Version metadata has no binding class: a JSON-like object or a Binding.
    metadata = env.VERSION
    assert isinstance(metadata, (Binding, dict)), type(metadata)
    assert metadata["id"] is not None
