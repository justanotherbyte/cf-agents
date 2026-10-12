import asyncio
from typing import Any, Generic, TypeVar

import pytest
from fake_js import JsValue, jsnull

from workers import DurableObject, WorkerEntrypoint, python_to_rpc
from workers.bindings import (
    Binding,
    D1Database,
    DurableObjectNamespace,
    Fetcher,
    KVNamespace,
    Queue,
    R2Bucket,
    Workflow,
    WorkflowInstance,
)


class Promise:
    """A settled JS promise: awaitable, and thenable like the real one."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def then(self, *callbacks: Any) -> None:
        pass

    def __await__(self) -> Any:
        return self.value
        yield


class JsBinding(JsValue):
    """A JS binding whose methods record their calls and return `returns`."""

    def __init__(
        self,
        name: str,
        returns: Any = None,
        sync: dict[str, Any] | None = None,
        **attributes: Any,
    ) -> None:
        super().__init__(name, **attributes)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.returns = returns
        self.sync = sync or {}

    def __getattr__(self, method: str) -> Any:
        if method.startswith("_"):
            raise AttributeError(method)

        def call(*args: Any) -> Any:
            self.calls.append((method, args))
            if method in self.sync:
                return self.sync[method]
            return Promise(self.returns)

        return call


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def worker_env(**bindings: Any) -> Any:
    """The env a WorkerEntrypoint sees when workerd passes these bindings."""
    return WorkerEntrypoint(None, JsValue("Object", **bindings)).env


# The env wrapper


def test_env_wraps_each_binding_by_its_js_class() -> None:
    env = worker_env(
        KV=JsBinding("KvNamespace"),
        DB=JsBinding("D1Database"),
        BUCKET=JsBinding("R2Bucket"),
        QUEUE=JsBinding("WorkerQueue"),
        COUNTER=JsBinding("DurableObjectNamespace"),
        OTHER=JsBinding("Fetcher"),
        FLOW=JsBinding("WorkflowImpl"),
        AI=JsBinding("Ai"),
        HYPERDRIVE=JsBinding("Hyperdrive"),
        TEXT="hello",
        FLAGS=JsValue("Object", {"beta": True}),
    )

    assert isinstance(env.KV, KVNamespace)
    assert isinstance(env.DB, D1Database)
    assert isinstance(env.BUCKET, R2Bucket)
    assert isinstance(env.QUEUE, Queue)
    assert isinstance(env.COUNTER, DurableObjectNamespace)
    assert isinstance(env.OTHER, Fetcher)
    assert isinstance(env.FLOW, Workflow)
    assert type(env.AI) is Binding
    assert type(env.HYPERDRIVE) is Binding
    assert env.TEXT == "hello"
    assert env.FLAGS == {"beta": True}
    assert env.KV is env.KV


def test_wrappers_expose_and_accept_their_js_object() -> None:
    js_kv = JsBinding("KvNamespace")
    kv = KVNamespace(js_kv)

    assert kv.js_object is js_kv
    assert KVNamespace(kv) is kv
    env = worker_env(KV=js_kv)
    assert WorkerEntrypoint(None, env).env is env


def test_env_and_bindings_are_sent_over_rpc_as_their_js_objects() -> None:
    # The stock SDK sends the env and namespace wrappers as PyProxies.
    js_counter = JsBinding("DurableObjectNamespace")
    env = worker_env(COUNTER=js_counter)

    assert python_to_rpc(env.COUNTER) is js_counter
    assert python_to_rpc(env) is env.js_object


# Typed methods


def test_kv_typed_methods_send_camel_case_options() -> None:
    js_kv = JsBinding("KvNamespace", returns="v")
    kv = KVNamespace(js_kv)

    run(kv.put("k", "v", expiration_ttl=60, metadata={"a": 1}))
    run(kv.put("k", "v", expirationTtl=60))
    assert run(kv.get("k")) == "v"

    assert js_kv.calls == [
        ("put", ("k", "v", {"expirationTtl": 60, "metadata": {"a": 1}})),
        ("put", ("k", "v", {"expirationTtl": 60})),
        ("get", ("k", {"type": "text"})),
    ]


def test_kv_get_converts_json_and_a_missing_key() -> None:
    assert run(KVNamespace(JsBinding("Kv", returns=jsnull)).get("k")) is None
    stored = JsValue("Object", {"n": JsValue("Array", [1, 2])})
    value = run(KVNamespace(JsBinding("Kv", returns=stored)).get("k", "json"))
    assert value == {"n": [1, 2]}


def test_d1_statements_bind_and_query() -> None:
    js_statement = JsBinding("D1PreparedStatement")
    js_statement.sync["bind"] = js_statement
    db = D1Database(JsBinding("D1Database", sync={"prepare": js_statement}))

    statement = db.prepare("SELECT ?").bind(1, None)
    js_statement.returns = JsValue("Object", {"results": JsValue("Array", [])})
    result = run(statement.all())
    run(statement.raw(column_names=True))

    assert result == {"results": []}
    assert js_statement.calls[0] == ("bind", (1, jsnull))
    assert js_statement.calls[-1] == ("raw", ({"columnNames": True},))


def test_queue_send_maps_options_and_forwards_unknown_keywords() -> None:
    js_queue = JsBinding("WorkerQueue")

    run(Queue(js_queue).send({"n": 1}, delay_seconds=5, contentType="json"))

    assert js_queue.calls == [
        ("send", ({"n": 1}, {"delaySeconds": 5, "contentType": "json"}))
    ]


def test_durable_object_stubs_call_rpc_methods_with_conversion() -> None:
    js_stub = JsBinding("DurableObject", returns=JsValue("Array", [jsnull]))
    js_namespace = JsValue("DurableObjectNamespace", getByName=lambda *a: js_stub)
    counter = DurableObjectNamespace(js_namespace).get_by_name("global")

    result = run(counter.increment({"by": None}))

    assert isinstance(counter, Fetcher)
    assert result == [None]
    assert js_stub.calls == [("increment", ({"by": jsnull},))]


def test_workflow_create_and_send_event() -> None:
    js_instance = JsBinding("WorkflowInstance", id="abc")
    js_workflow = JsBinding("WorkflowImpl", returns=js_instance)

    instance = run(Workflow(js_workflow).create(id="abc", params={"p": 1}))
    run(instance.send_event(type="ready", payload={"n": None}))

    assert isinstance(instance, WorkflowInstance)
    assert instance.id == "abc"
    assert js_workflow.calls == [("create", ({"id": "abc", "params": {"p": 1}},))]
    assert js_instance.calls == [
        ("sendEvent", ({"type": "ready", "payload": {"n": jsnull}},))
    ]


# Passthrough


def test_names_without_a_typed_method_pass_through_under_their_js_name() -> None:
    js_kv = JsBinding("KvNamespace", returns=JsValue("Object", {"value": "v"}))

    result = run(KVNamespace(js_kv).getWithMetadata("k", None))

    assert result == {"value": "v"}
    assert js_kv.calls == [("getWithMetadata", ("k", jsnull))]


def test_plain_attributes_are_read_on_every_access() -> None:
    # The stock SDK caches the first value, so a D1 cursor's counters freeze.
    cursor = JsValue("Cursor", rowsRead=1)
    wrapped = Binding(cursor)

    assert wrapped.rowsRead == 1
    cursor.__dict__["rowsRead"] = 2
    assert wrapped.rowsRead == 2


# Generics


def test_generic_mixes_in_before_or_after_an_entrypoint() -> None:
    S = TypeVar("S")

    # Runtime only: a type checker sees DurableObject as generic and reports
    # an inconsistent MRO, so typed code puts Generic last.
    class Before(Generic[S], DurableObject):  # noqa: PYI059  # ty: ignore[inconsistent-mro]
        pass

    class After(DurableObject, Generic[S]):
        pass

    class Parameterized(DurableObject[Any]):
        pass

    assert issubclass(Before, DurableObject)
    assert issubclass(After, DurableObject)
    assert issubclass(Parameterized, DurableObject)


# Stock call forms: positional options as a dict or a JS object


def test_typed_methods_accept_positional_options_as_the_stock_sdk_did() -> None:
    js_kv = JsBinding("KvNamespace")
    kv = KVNamespace(js_kv)
    js_options = JsValue("Object")

    run(kv.list({"prefix": "a"}))
    run(kv.get("k", {"type": "json"}))
    run(kv.get("k", "json"))
    run(kv.get(["a", "b"]))
    run(kv.put("k", "v", js_options))

    assert js_kv.calls == [
        ("list", ({"prefix": "a"},)),
        ("get", ("k", {"type": "json"})),
        ("get", ("k", {"type": "json"})),
        ("get", (["a", "b"], {"type": "text"})),
        ("put", ("k", "v", js_options)),
    ]


def test_d1_raw_takes_every_stock_form() -> None:
    js_statement = JsBinding("D1PreparedStatement")
    statement = D1Database(
        JsBinding("D1Database", sync={"prepare": js_statement})
    ).prepare("SELECT 1")

    run(statement.raw(column_names=True))
    run(statement.raw(columnNames=True))
    run(statement.raw({"columnNames": True}))
    run(statement.raw(None))

    assert [args for _, args in js_statement.calls] == [
        ({"columnNames": True},),
        ({"columnNames": True},),
        ({"columnNames": True},),
        ({},),
    ]


def test_a_js_options_object_and_keywords_cannot_mix() -> None:
    kv = KVNamespace(JsBinding("KvNamespace"))

    with pytest.raises(TypeError):
        run(kv.put("k", "v", JsValue("Object"), expiration_ttl=60))


def test_durable_object_ids_and_names_stay_raw_as_in_the_stock_sdk() -> None:
    js_id = JsValue("DurableObjectId")
    js_stub = JsBinding("DurableObject")
    js_namespace = JsValue(
        "DurableObjectNamespace",
        idFromName=lambda name: js_id,
        getByName=lambda *args: js_stub,
    )
    namespace = DurableObjectNamespace(js_namespace)

    assert namespace.id_from_name("x") is js_id
    assert namespace.idFromName("x") is js_id
    assert isinstance(namespace.getByName("x"), Fetcher)


def test_workflow_events_and_options_leave_out_unset_fields() -> None:
    js_instance = JsBinding("WorkflowInstance")
    js_workflow = JsBinding("WorkflowImpl", returns=js_instance)

    instance = run(Workflow(js_workflow).create({"params": {"p": 1}}, id="abc"))
    run(instance.send_event(type="ready"))

    assert js_workflow.calls == [("create", ({"id": "abc", "params": {"p": 1}},))]
    assert js_instance.calls == [("sendEvent", ({"type": "ready"},))]
