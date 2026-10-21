import asyncio
from datetime import datetime
from typing import Any

import pytest
from fake_js import MICROTASKS, PATCHED_CONTEXTS, JsBinding, JsValue, jsnull, run

from workers import DurableObject, WorkerEntrypoint
from workers.bindings import Binding, KVNamespace
from workers.context import (
    DurableObjectContext,
    DurableObjectStorage,
    ExecutionContext,
)
from workers.entrypoints import DurableObjectAbort


def js_state(**storage: Any) -> JsValue:
    return JsValue(
        "DurableObjectState",
        storage=JsBinding("DurableObjectStorage", **storage),
        id=JsValue("DurableObjectId"),
    )


def js_env() -> JsValue:
    return JsValue("Object", KV=JsBinding("KvNamespace"))


def test_entrypoints_wrap_ctx_by_kind_and_patch_wait_until() -> None:
    js_context = JsBinding("ExecutionContext")
    state = js_state()

    worker = WorkerEntrypoint(js_context, js_env())
    durable = DurableObject(state, js_env())

    assert isinstance(worker.ctx, ExecutionContext)
    assert worker.ctx.js_object is js_context
    assert isinstance(durable.ctx, DurableObjectContext)
    assert PATCHED_CONTEXTS[-2:] == [js_context, state]


def test_a_user_init_receives_wrapped_ctx_and_env() -> None:
    seen = []

    class Counter(DurableObject):
        def __init__(self, ctx: Any, env: Any) -> None:
            seen.append((type(ctx), type(env.KV)))
            super().__init__(ctx, env)

    Counter(js_state(), js_env())

    assert seen == [(DurableObjectContext, KVNamespace)]


def test_wait_until_hands_the_coroutine_to_js() -> None:
    js_context = JsBinding("ExecutionContext")

    async def background() -> None:
        pass

    async def run_wait_until() -> None:
        ExecutionContext(js_context).wait_until(background())
        ((_, (promise,)),) = js_context.calls
        await promise.source.proxied

    asyncio.run(run_wait_until())


def test_other_context_names_pass_through_raw_with_wrappers_unwrapped() -> None:
    js_socket = JsValue("WebSocket")
    accepted = []
    state = JsValue("DurableObjectState", acceptWebSocket=accepted.append, n=1)
    ctx = DurableObjectContext(state)

    ctx.acceptWebSocket(Binding(js_socket))
    ctx.accept_web_socket(Binding(js_socket))

    assert accepted == [js_socket, js_socket]
    assert ctx.n == 1


def test_abort_queues_the_js_abort_and_raises_past_except_exception() -> None:
    aborted = []
    ctx = DurableObjectContext(JsValue("DurableObjectState", abort=aborted.append))

    with pytest.raises(DurableObjectAbort, match="bye"):
        try:
            ctx.abort("bye")
        except Exception:  # noqa: BLE001
            pytest.fail("DurableObjectAbort must not be an Exception")

    MICROTASKS.pop()()
    assert aborted == ["bye"]


def test_storage_typed_methods_convert_values_and_options() -> None:
    js_storage = JsBinding("DurableObjectStorage", returns=JsValue("Map", {"a": 1}))
    storage = DurableObjectStorage(js_storage)

    assert run(storage.get(["a"])) == {"a": 1}
    run(storage.put({"a": 1, "b": None}))
    run(storage.put("k", {"v": None}, allow_unconfirmed=True, other_option=1))
    run(storage.list(prefix="a", start_after="a0"))
    run(storage.delete_all())
    run(storage.set_alarm(datetime.fromtimestamp(60)))

    assert js_storage.calls == [
        ("get", (["a"], {})),
        ("put", ({"a": 1, "b": jsnull}, {})),
        ("put", ("k", {"v": jsnull}, {"allowUnconfirmed": True, "other_option": 1})),
        ("list", ({"prefix": "a", "startAfter": "a0"},)),
        ("deleteAll", ({},)),
        ("setAlarm", (60_000, {})),
    ]


def test_storage_camel_case_names_still_pass_through() -> None:
    js_storage = JsBinding("DurableObjectStorage", returns=jsnull)

    assert run(DurableObjectStorage(js_storage).deleteAll()) is None
    assert js_storage.calls == [("deleteAll", ())]


def test_ctx_storage_is_typed() -> None:
    assert isinstance(DurableObjectContext(js_state()).storage, DurableObjectStorage)


def test_alarm_and_web_socket_handlers_receive_python_values() -> None:
    received = []

    class Mixin:
        async def alarm(self, info: Any) -> None:
            received.append(info)

    class Room(Mixin, DurableObject):
        async def webSocketMessage(self, ws: Any, message: Any) -> None:  # noqa: N802
            received.append((ws, message))

    room = Room(js_state(), js_env())
    info = JsValue("Object", {"retryCount": 0, "isRetry": False})
    socket = JsValue("WebSocket")

    asyncio.run(room.alarm(info))
    asyncio.run(getattr(room, "on_alarm")(info))  # noqa: B009 (set at runtime)
    asyncio.run(room.webSocketMessage(socket, "hi"))

    assert received[0] == {"retryCount": 0, "isRetry": False}
    assert received[1] == received[0]
    ws, message = received[2]
    assert isinstance(ws, Binding)
    assert ws.js_object is socket
    assert message == "hi"


def test_queue_batches_arrive_wrapped() -> None:
    received = []

    class Consumer(WorkerEntrypoint):
        async def queue(self, batch: Any) -> None:
            received.append(batch)

    batch = JsValue("MessageBatch")
    asyncio.run(Consumer(JsBinding("ExecutionContext"), js_env()).queue(batch))

    assert isinstance(received[0], Binding)
    assert received[0].js_object is batch


def test_a_user_defined_legacy_handler_also_receives_python_values() -> None:
    received = []

    class Room(DurableObject):
        async def on_alarm(self, info: Any) -> None:
            received.append(info)

    asyncio.run(Room(js_state(), js_env()).on_alarm(JsValue("Object", {"n": 1})))

    assert received == [{"n": 1}]


def test_a_binary_web_socket_message_stays_an_array_buffer() -> None:
    received = []

    class Room(DurableObject):
        async def webSocketMessage(self, ws: Any, message: Any) -> None:  # noqa: N802
            received.append(message)

    message = JsValue("ArrayBuffer", b"hi")

    asyncio.run(
        Room(js_state(), js_env()).webSocketMessage(JsValue("WebSocket"), message)
    )

    # As in the stock SDK, so stock code can call message.to_bytes().
    assert received == [message]
