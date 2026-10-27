import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from _scripted import MODERN_DISCOVER, Reply, ScriptedTransport, error, result
from _servers import URL, demo_server, serve
from mcp.server.mcpserver import Context, MCPServer
from test_session import legacy_only

from agents.mcp.connection import MCPClientConnection
from agents.mcp.errors import MCPServerNotReadyError
from agents.mcp.http import HttpRequest, HttpTransport
from agents.mcp.models import TextContent
from agents.mcp.types import MCPConnectionState


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.changes = 0
        self.authorized: list[MCPClientConnection] = []

    def connection(self, http: HttpTransport, url: str = URL) -> MCPClientConnection:
        def changed() -> None:
            self.changes += 1

        return MCPClientConnection(
            "srv",
            url,
            http=http,
            client_info={"name": "c", "version": "1"},
            headers={},
            emit=lambda type, payload: self.events.append((type, payload)),
            on_change=changed,
            on_authorize=self.authorized.append,
        )


async def eventually(check: Callable[[], bool]) -> None:
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


def growing_server() -> MCPServer:
    server = demo_server()

    @server.tool()
    async def grow(ctx: Context) -> str:
        """Add a tool and announce it."""
        server.add_tool(lambda: "new", name="new_tool", description="New.")
        await ctx.notify_tools_changed()  # 2026-07-28: subscriptions/listen
        await ctx.session.send_tool_list_changed()  # handshake era: the GET stream
        return "grown"

    return server


@pytest.mark.parametrize("era", ["modern", "handshake"])
def test_connect_discover_and_call(era: str) -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            if era == "handshake":
                transport.app = legacy_only(transport.app)
            recorder = Recorder()
            connection = recorder.connection(transport)
            assert await connection.init() == MCPConnectionState.CONNECTED
            await connection.discover(timeout=5)
            assert connection.state == MCPConnectionState.READY
            assert {tool["name"] for tool in connection.tools} == {"add", "fail"}
            assert [p["name"] for p in connection.prompts] == ["greet"]
            assert [r["uri"] for r in connection.resources] == ["note://readme"]
            assert connection.resource_templates[0]["uriTemplate"] == "note://{id}"
            assert connection.instructions == "Be nice."

            result = await connection.call_tool("add", {"a": 2, "b": 5})
            assert result.content == [TextContent(text="7")]
            assert not result.is_error
            prompt = await connection.get_prompt("greet", {"name": "Bo"})
            assert prompt.messages[0].role == "user"
            read = await connection.read_resource("note://7")
            assert read.contents[0].uri == "note://7"

            await connection.close()
            types = [type for type, _ in recorder.events]
            assert types == [
                "mcp:client:connect",
                "mcp:client:discover",
                "mcp:client:close",
            ]
            assert recorder.events[0][1] == {
                "url": URL,
                "transport": "streamable-http",
                "state": "connected",
            }

    asyncio.run(main())


@pytest.mark.parametrize("era", ["modern", "handshake"])
def test_list_changed_refreshes_tools(era: str) -> None:
    async def main() -> None:
        async with serve(growing_server()) as transport:
            if era == "handshake":
                transport.app = legacy_only(transport.app)
            recorder = Recorder()
            connection = recorder.connection(transport)
            await connection.init()
            await connection.discover(timeout=5)
            # The listener must be open before the server announces anything.
            await asyncio.sleep(0.1)
            await connection.call_tool("grow", {})
            await eventually(
                lambda: "new_tool" in {tool["name"] for tool in connection.tools}
            )
            await connection.close()

    asyncio.run(main())


def test_calls_need_ready() -> None:
    connection = Recorder().connection(ScriptedTransport(lambda r, m: None))
    with pytest.raises(MCPServerNotReadyError):
        asyncio.run(connection.call_tool("x"))


def test_unreachable_server_fails() -> None:
    recorder = Recorder()
    connection = recorder.connection(ScriptedTransport(lambda r, m: None))
    assert asyncio.run(connection.init()) == MCPConnectionState.FAILED
    assert connection.error is not None
    assert "connection refused" in connection.error
    assert recorder.events[-1][1]["state"] == "failed"


def test_not_streamable_http_names_sse() -> None:
    connection = Recorder().connection(
        ScriptedTransport(lambda r, m: Reply(status=404))
    )
    asyncio.run(connection.init())
    assert connection.state == MCPConnectionState.FAILED
    assert connection.error is not None
    assert "legacy SSE" in connection.error


def test_discovery_failure_returns_to_connected() -> None:
    def answer(request: HttpRequest, method: str | None) -> Reply:
        if method == "server/discover":
            return result(
                request,
                {**MODERN_DISCOVER, "capabilities": {"tools": {}, "resources": {}}},
            )
        if method == "resources/templates/list":
            return error(request, -32601)  # advertised but missing: tolerated
        if method == "tools/list":
            return error(request, -32603)
        return result(request, {"resources": []})

    recorder = Recorder()
    connection = recorder.connection(ScriptedTransport(answer))

    async def main() -> None:
        await connection.init()
        await connection.discover(timeout=5)

    asyncio.run(main())
    assert connection.state == MCPConnectionState.CONNECTED
    assert connection.error is not None
    discover_events = [p for t, p in recorder.events if t == "mcp:client:discover"]
    assert {
        "url": connection.url,
        "capability": "resources",
        "error": discover_events[0]["error"],
    } in discover_events
    assert "error" in discover_events[-1]


def test_discovery_timeout() -> None:
    async def main() -> None:
        server = demo_server()
        async with serve(server) as transport:
            original = transport.app

            async def slow_lists(scope: Any, receive: Any, send: Any) -> None:
                message = await receive()
                if b'"tools/list"' in message.get("body", b""):
                    await asyncio.sleep(5)

                replayed = False

                async def replay() -> Any:
                    nonlocal replayed
                    if replayed:
                        return await receive()
                    replayed = True
                    return message

                await original(scope, replay, send)

            transport.app = slow_lists
            connection = Recorder().connection(transport)
            await connection.init()
            await connection.discover(timeout=0.2)
            assert connection.state == MCPConnectionState.CONNECTED
            assert connection.error is not None
            assert "timed out" in connection.error

    asyncio.run(main())
