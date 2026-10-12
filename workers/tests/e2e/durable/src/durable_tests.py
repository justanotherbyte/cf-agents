"""Each test gets the Default entrypoint, with its wrapped ctx and env."""

import asyncio

from pyodide.ffi import JsException, create_proxy

from workers.context import ExecutionContext


def room(worker, name):
    return worker.env.ROOM.get_by_name(name)


async def test_user_init_sees_wrapped_ctx_and_env(worker):
    seen = await room(worker, "init").init_types()
    assert seen == ["DurableObjectContext", "KVNamespace"], seen


async def test_storage(worker):
    assert await room(worker, "storage").storage_roundtrip() == "ok"


async def test_id_and_block_concurrency_while(worker):
    assert len(await room(worker, "id").identity()) == 64
    assert await room(worker, "id").blocked() == 5


async def test_alarm_receives_python_values(worker):
    stub = room(worker, "alarm")
    await stub.schedule_alarm()
    for _ in range(50):
        seen = await stub.alarm_seen()
        if seen is not None:
            break
        await asyncio.sleep(0.1)
    assert seen == {"type": "Binding", "retry": 0}, seen


async def test_web_socket_handler_receives_a_wrapper(worker):
    response = await room(worker, "ws").fetch(
        "http://room/", headers={"Upgrade": "websocket"}
    )
    assert response.status == 101
    socket = response.js_object.webSocket
    socket.accept()
    received = asyncio.get_running_loop().create_future()
    listener = create_proxy(lambda event: received.set_result(event.data))
    socket.addEventListener("message", listener)
    socket.send("hi")
    assert await asyncio.wait_for(received, 5) == "hi Binding 1"
    socket.close()
    listener.destroy()


async def test_abort_resets_the_object(worker):
    stub = room(worker, "abort")
    assert await stub.bump() == 1
    try:
        await stub.explode()
    except JsException as error:
        assert "bye" in str(error), str(error)
    else:
        raise AssertionError("expected the abort to reach the caller")
    assert await room(worker, "abort").bump() == 1


async def test_durable_object_wait_until_keeps_a_coroutine_alive(worker):
    stub = room(worker, "defer")
    await stub.defer()
    for _ in range(30):
        if await stub.deferred():
            break
        await asyncio.sleep(0.05)
    assert await stub.deferred() is True


async def test_wait_until_keeps_a_coroutine_alive(worker):
    assert isinstance(worker.ctx, ExecutionContext)
    finished = []

    async def later():
        await asyncio.sleep(0.05)
        finished.append(True)

    worker.ctx.wait_until(later())
    assert finished == []
    await asyncio.sleep(0.2)
    assert finished == [True]
