"""Mirrors ``fixtures.ts``: a mocked Durable Object ctx and container fixtures."""

import asyncio
import sys
import time
from collections.abc import AsyncIterator, Callable, Mapping
from types import SimpleNamespace
from typing import Any
from unittest.mock import DEFAULT, AsyncMock, MagicMock

import pytest
from test_setup import JsResponse

from containers import Container


class MockWebSocket:
    def __init__(self) -> None:
        self.event_listeners: dict[str, list[Callable[[Any], Any]]] = {
            "message": [],
            "close": [],
            "error": [],
        }
        self.accept = MagicMock()
        self.send = MagicMock()
        self.close = MagicMock()

    def addEventListener(self, type: str, handler: Callable[[Any], Any]) -> None:  # noqa: N802
        self.event_listeners[type].append(handler)


class ObjectContaining:
    """Like vitest's ``expect.objectContaining``."""

    def __init__(self, **expected: Any) -> None:
        self.expected = expected

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Mapping) and all(
            key in other and other[key] == value for key, value in self.expected.items()
        )

    def __repr__(self) -> str:
        return f"ObjectContaining({self.expected!r})"


async def wait_for(assertion: Callable[[], None], timeout: float = 1.0) -> None:
    """Like vitest's ``vi.waitFor``: retry ``assertion`` until it passes."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            assertion()
            return
        except AssertionError:
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.05)


def yielding_async_mock(return_value: Any = None) -> AsyncMock:
    """
    An AsyncMock that yields to the event loop before returning, like real DO
    storage. A plain AsyncMock completes without suspending.
    """

    async def side_effect(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0)
        return DEFAULT

    return AsyncMock(return_value=return_value, side_effect=side_effect)


def _make_tcp_fetch() -> AsyncMock:
    """A tcp port fetch mock that records resolved values, like vitest's results."""
    fetch = AsyncMock()
    fetch.results = []

    async def default_fetch(url: str, init: Any = None) -> JsResponse:
        headers = getattr(init, "headers", None)
        if headers is not None and headers.get("Upgrade") == "websocket":
            response = JsResponse(status=200, web_socket=MockWebSocket(), headers={})
        else:
            response = JsResponse(status=200, web_socket=None, body=None)
        fetch.results.append(response)
        return response

    fetch.side_effect = default_fetch
    return fetch


def make_mock_ctx() -> MagicMock:
    loop = asyncio.get_running_loop()
    ctx = MagicMock()

    ctx.storage.get = yielding_async_mock(None)
    ctx.storage.put = yielding_async_mock(None)
    ctx.storage.delete = yielding_async_mock(True)
    ctx.storage.setAlarm = yielding_async_mock(None)
    ctx.storage.deleteAlarm = yielding_async_mock(None)
    ctx.storage.sync = yielding_async_mock(None)
    ctx.storage.kv.get = MagicMock(return_value=None)
    ctx.storage.kv.put = MagicMock(return_value=None)
    ctx.storage.kv.delete = MagicMock(return_value=True)
    ctx.storage.sql.exec = MagicMock(return_value=[])

    # Tasks started by blockConcurrencyWhile, kept so they aren't garbage collected
    ctx.block_concurrency_tasks = []

    def block_concurrency_while(fn: Callable[[], Any]) -> asyncio.Future[Any]:
        task = asyncio.ensure_future(fn())
        ctx.block_concurrency_tasks.append(task)
        return task

    ctx.blockConcurrencyWhile = MagicMock(side_effect=block_concurrency_while)
    ctx.abort = MagicMock()
    ctx.id.toString = MagicMock(return_value="test-container-id")
    ctx.exports.ContainerProxy = MagicMock(
        return_value=SimpleNamespace(fetch=MagicMock())
    )

    container = ctx.container
    container.running = False

    def start(*args: Any) -> None:
        container.running = True

    container.start = MagicMock(side_effect=start)
    container.signal = MagicMock()
    container.destroy = AsyncMock()
    container.monitor = MagicMock(side_effect=lambda: loop.create_future())
    container.interceptOutboundHttp = yielding_async_mock(None)
    container.interceptOutboundHttps = yielding_async_mock(None)
    container.interceptAllOutboundHttp = yielding_async_mock(None)
    container.getTcpPort = MagicMock(
        return_value=SimpleNamespace(fetch=_make_tcp_fetch())
    )

    return ctx


@pytest.fixture
def web_socket_pair_spy(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    spy = MagicMock(side_effect=lambda: {0: MockWebSocket(), 1: MockWebSocket()})
    monkeypatch.setattr(sys.modules["js"].WebSocketPair, "new", spy)
    return spy


@pytest.fixture
async def mock_ctx(web_socket_pair_spy: MagicMock) -> AsyncIterator[MagicMock]:
    ctx = make_mock_ctx()
    yield ctx
    for task in ctx.block_concurrency_tasks:
        task.cancel()


@pytest.fixture
async def container(mock_ctx: MagicMock) -> Container:
    container = Container(mock_ctx, {})
    container.default_port = 8080
    # Let the constructor's blockConcurrencyWhile callback finish, as the JS
    # constructor runs it eagerly up to its first await.
    await asyncio.gather(*mock_ctx.block_concurrency_tasks)
    return container
