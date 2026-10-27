"""MCP on an Agent: options, members, the frame and its broadcasts."""

import asyncio
import json
from typing import Any

import pytest
from _servers import URL, demo_server, serve
from fake_runtime import FakeCtx, FakeWebSocket
from workers import Request, Response

from agents import Agent, AgentOptions, MCPNotEnabledError, MCPOptions


class McpAgent(Agent):
    options = AgentOptions(mcp=MCPOptions(client_name="tester"))

    async def on_request(self, request: Request) -> Response:
        result = await self.add_mcp_server("Demo", URL, id="demo")
        [record] = self.mcp.list_servers()
        return Response(
            json.dumps({"state": result.state, "callback": record.callback_url})
        )


class Plain(Agent):
    pass


class Private(McpAgent):
    options = AgentOptions(send_identity_on_connect=False, mcp=MCPOptions())


async def connect(agent: Agent[Any]) -> FakeWebSocket:
    request = Request(
        "https://example.com/agents/mcp-agent/room-1?_pk=a",
        headers={"Upgrade": "websocket"},
    )
    response = await agent.fetch(request)  # ty: ignore[unresolved-attribute]
    return response.web_socket.peer


def mcp_frames(ws: FakeWebSocket) -> list[dict[str, Any]]:
    return [f["mcp"] for f in ws.frames() if f["type"] == "cf_agent_mcp_servers"]


def test_mcp_off() -> None:
    agent = Plain(FakeCtx("room-1"), env=None)
    with pytest.raises(MCPNotEnabledError):
        agent.mcp  # noqa: B018
    with pytest.raises(MCPNotEnabledError):
        agent.get_mcp_servers()


def test_add_server_from_a_request_broadcasts_and_derives_the_callback() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            agent = McpAgent(FakeCtx("room-1"), env=None)
            agent.mcp.http = transport
            ws = await connect(agent)
            assert mcp_frames(ws)[-1]["servers"] == {}

            response = await agent.fetch(  # ty: ignore[unresolved-attribute]
                Request("https://example.com/agents/mcp-agent/room-1/add")
            )
            body = json.loads(response.body)
            assert body == {
                "state": "ready",
                "callback": "https://example.com/agents/mcp-agent/room-1/callback",
            }

            last = mcp_frames(ws)[-1]
            assert last["servers"]["demo"]["state"] == "ready"
            assert {t["name"] for t in last["tools"]} == {"add", "fail"}
            assert agent.get_mcp_servers().servers["demo"].state == "ready"
            assert agent.mcp.connections["demo"]._client_info == {
                "name": "tester",
                "version": __import__("agents").__version__,
            }

    asyncio.run(main())


def test_callback_path_is_required_without_identity() -> None:
    async def main() -> None:
        agent = Private(FakeCtx("room-1"), env=None)
        await agent.lifecycle.start()
        with pytest.raises(ValueError, match="callback_path"):
            await agent.add_mcp_server("Demo", URL, callback_host="https://example.com")

    asyncio.run(main())


def test_restored_servers_broadcast_once_started() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            first = McpAgent(FakeCtx("room-1"), env=None)
            first.mcp.http = transport
            await first.lifecycle.start()
            await first.add_mcp_server("Demo", URL, id="demo")

            ctx = FakeCtx("room-1", storage=first.ctx.storage)
            second = McpAgent(ctx, env=None)
            second.mcp.http = transport
            ws = await connect(second)
            await second.mcp.wait_for_connections(timeout=5)
            assert mcp_frames(ws)[-1]["servers"]["demo"]["state"] == "ready"

    asyncio.run(main())
