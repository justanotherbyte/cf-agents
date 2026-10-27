"""The session against the MCP SDK's own server, in both eras."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest
from _asgi_transport import AsgiTransport
from _servers import URL, demo_server, serve

from agents.mcp.errors import MCPRpcError
from agents.mcp.session import McpSession
from agents.mcp.types import SessionSnapshot

CLIENT = {"name": "test-client", "version": "1.0"}


def session(transport: AsgiTransport, **kwargs: Any) -> McpSession:
    notifications: list[tuple[str, dict[str, Any]]] = kwargs.pop("notifications", [])
    return McpSession(
        URL,
        http=transport,
        client_info=CLIENT,
        headers=kwargs.pop("headers", {}),
        auth=None,
        on_notification=lambda method, params: notifications.append((method, params)),
    )


def legacy_only(app: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap an app so server/discover is a 404, as on a pre-2026 server."""

    async def wrapped(scope: dict[str, Any], receive: Any, send: Any) -> None:
        message = await receive()
        if b'"server/discover"' in message.get("body", b""):
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        replayed = False

        async def replay() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return message
            return await receive()

        await app(scope, replay, send)

    return wrapped


async def exercise(mcp: McpSession) -> None:
    tools = await mcp.list_all("tools/list", "tools")
    assert {tool["name"] for tool in tools} == {"add", "fail"}
    result = await mcp.call_tool("add", {"a": 1, "b": 2})
    assert result["content"][0]["text"] == "3"
    failed = await mcp.call_tool("fail", {})
    assert failed["isError"] is True
    prompt = await mcp.get_prompt("greet", {"name": "Ada"})
    assert prompt["messages"][0]["content"]["text"] == "Hello, Ada!"
    read = await mcp.read_resource("note://readme")
    assert read["contents"][0]["text"] == "read me"
    templates = await mcp.list_all("resources/templates/list", "resourceTemplates")
    assert templates[0]["uriTemplate"] == "note://{id}"


@pytest.mark.parametrize("json_response", [True, False])
def test_modern_era(json_response: bool) -> None:
    async def main() -> None:
        async with serve(demo_server(), json_response=json_response) as transport:
            mcp = session(transport)
            await mcp.connect()
            assert mcp.protocol_version == "2026-07-28"
            assert mcp.session_id is None
            assert mcp.instructions == "Be nice."
            assert "tools" in mcp.capabilities
            await exercise(mcp)
            # The SDK server checks the envelope's headers exactly.
            call = next(
                r
                for r in transport.requests
                if b"tools/call" in (r.body or "").encode()
            )
            assert call.headers["mcp-method"] == "tools/call"
            assert call.headers["mcp-name"] == "add"

    asyncio.run(main())


@pytest.mark.parametrize("json_response", [True, False])
def test_handshake_era(json_response: bool) -> None:
    async def main() -> None:
        server = demo_server()
        async with serve(server, json_response=json_response) as transport:
            transport.app = legacy_only(transport.app)
            mcp = session(transport)
            await mcp.connect()
            assert mcp.protocol_version == "2025-11-25"
            assert mcp.session_id
            await exercise(mcp)
            assert all(
                r.headers.get("mcp-session-id") == mcp.session_id
                for r in transport.requests[3:]
            )
            await mcp.close()
            assert transport.requests[-1].method == "DELETE"
            assert mcp.session_id is None

    asyncio.run(main())


def test_stateless_handshake() -> None:
    async def main() -> None:
        async with serve(demo_server(), stateless_http=True) as transport:
            transport.app = legacy_only(transport.app)
            mcp = session(transport)
            await mcp.connect()
            assert mcp.protocol_version == "2025-11-25"
            assert mcp.session_id is None
            await exercise(mcp)

    asyncio.run(main())


def test_modern_restore_makes_no_request() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            first = session(transport)
            await first.connect()
            snapshot = first.snapshot
            assert snapshot is not None
            assert snapshot.discover_result is not None
            count = len(transport.requests)

            restored = session(transport)
            await restored.connect(prior=snapshot)
            assert len(transport.requests) == count
            assert restored.capabilities == first.capabilities
            assert (await restored.call_tool("add", {"a": 2, "b": 2}))["content"][0][
                "text"
            ] == "4"

    asyncio.run(main())


def test_handshake_restore_handshakes_again() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            transport.app = legacy_only(transport.app)
            mcp = session(transport)
            await mcp.connect(
                prior=SessionSnapshot(protocol_version="2025-11-25", session_id="old")
            )
            assert mcp.session_id not in (None, "old")

    asyncio.run(main())


def test_expired_session_reinitializes() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            transport.app = legacy_only(transport.app)
            mcp = session(transport)
            await mcp.connect()
            first = mcp.session_id
            mcp.session_id = "forgotten"  # as if the server restarted
            result = await mcp.call_tool("add", {"a": 1, "b": 1})
            assert result["content"][0]["text"] == "2"
            assert mcp.session_id not in (None, first, "forgotten")

    asyncio.run(main())


def test_rpc_error_and_timeout() -> None:
    async def main() -> None:
        server = demo_server()

        @server.tool()
        async def slow() -> str:
            """Takes a while."""
            await asyncio.sleep(5)
            return "done"

        async with serve(server, json_response=False) as transport:
            mcp = session(transport)
            await mcp.connect()
            with pytest.raises(MCPRpcError) as raised:
                await mcp.request("no/such/method")
            assert raised.value.code == -32601
            with pytest.raises(MCPRpcError) as raised:
                await mcp.call_tool("slow", {}, timeout=0.2)
            assert raised.value.code == -32001
            assert transport.disconnects >= 1

    asyncio.run(main())


def test_param_headers() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            mcp = session(transport)
            await mcp.connect()
            mcp._remember_tools(
                [
                    {
                        "name": "t",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "region": {"type": "string", "x-mcp-header": "Region"},
                                "flag": {"type": "boolean", "x-mcp-header": "Flag"},
                                "nested": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string", "x-mcp-header": "Id"}
                                    },
                                },
                            },
                        },
                    }
                ]
            )
            _, headers = mcp._stamp(
                "tools/call",
                {
                    "name": "t",
                    "arguments": {"region": "é", "flag": True, "nested": {"id": "7"}},
                },
            )
            assert headers["Mcp-Param-Region"] == "=?base64?w6k=?="
            assert headers["Mcp-Param-Flag"] == "true"
            assert headers["Mcp-Param-Id"] == "7"

    asyncio.run(main())


def test_jsonrpc_body_is_json() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            mcp = session(transport)
            await mcp.connect()
            body = json.loads(transport.requests[0].body or "")
            assert body["method"] == "server/discover"
            assert (
                body["params"]["_meta"]["io.modelcontextprotocol/clientInfo"] == CLIENT
            )

    asyncio.run(main())
