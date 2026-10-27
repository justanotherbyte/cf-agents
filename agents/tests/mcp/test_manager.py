import asyncio
import json
from typing import Any

import pytest
from _asgi_transport import AsgiTransport
from _scripted import Reply, ScriptedTransport
from _servers import URL, demo_server, serve
from fake_runtime import Host

from agents.core.events import Disposable
from agents.lifecycle import Lifecycle, LifecycleEvent
from agents.mcp.errors import (
    MCPBlockedUrlError,
    MCPConnectionError,
    MCPServerIdConflictError,
    MCPServerNotFoundError,
)
from agents.mcp.manager import MCPClientManager
from agents.mcp.models import TextContent
from agents.mcp.types import MCPConnectionState


class Setup:
    def __init__(self, http: Any, *, host: Host | None = None) -> None:
        self.leases = 0
        self.released = 0
        self.changes = 0
        self.manager = MCPClientManager(
            client_name="TestAgent",
            client_version="1.0",
            keep_alive=self.lease,
            http=http,
        )
        self.host = host or Host()
        self.lifecycle = Lifecycle(self.host).use(self.manager)
        self.events: list[LifecycleEvent] = []
        self.lifecycle._set_event_sink(self.events.append)
        self.manager.on_server_state_changed(lambda _: self.count())

    def count(self) -> None:
        self.changes += 1

    async def lease(self) -> Disposable:
        self.leases += 1

        def release() -> None:
            self.released += 1

        return Disposable(release)

    def event_types(self) -> list[str]:
        return [event.type for event in self.events]


def rows(host: Host) -> list[dict[str, Any]]:
    return host.ctx.storage.sql.exec("SELECT * FROM cf_agents_mcp_servers").toArray()


def test_add_call_and_frame() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            setup = Setup(transport)
            await setup.lifecycle.start()
            result = await setup.manager.add_server("Demo", URL, id="Demo Server")
            assert (result.id, result.state, result.auth_url) == (
                "demo-server",
                "ready",
                None,
            )

            tools = setup.manager.list_tools()
            assert sorted(t.tool.name for t in tools) == ["add", "fail"]
            assert {t.server_id for t in tools} == {"demo-server"}
            called = await setup.manager.call_tool(
                "demo-server", "add", {"a": 1, "b": 1}
            )
            assert called.content == [TextContent(text="2")]

            state = setup.manager.servers_state()
            info = state.servers["demo-server"]
            assert (info.name, info.state, info.instructions) == (
                "Demo",
                "ready",
                "Be nice.",
            )
            assert {t["serverId"] for t in state.tools} == {"demo-server"}
            assert [r["uri"] for r in state.resources] == ["note://readme"]

            [row] = rows(setup.host)
            options = json.loads(row["server_options"])
            assert row["server_url"] == URL
            assert options["transport"]["protocolVersion"] == "2026-07-28"
            assert "discoverResult" in options
            assert "mcp:client:connect" in setup.event_types()
            assert setup.changes > 0

    asyncio.run(main())


def test_readd_is_idempotent_and_ids_conflict() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            setup = Setup(transport)
            await setup.lifecycle.start()
            first = await setup.manager.add_server("Demo", URL)
            again = await setup.manager.add_server("Demo", URL)
            assert again.id == first.id
            assert len(rows(setup.host)) == 1

            await setup.manager.add_server("Demo", URL, id="fixed")
            with pytest.raises(MCPServerIdConflictError):
                await setup.manager.add_server("Other", URL, id="fixed")

    asyncio.run(main())


def test_blocked_url_is_refused_before_storing() -> None:
    async def main() -> None:
        setup = Setup(ScriptedTransport(lambda r, m: None))
        await setup.lifecycle.start()
        with pytest.raises(MCPBlockedUrlError):
            await setup.manager.add_server("Meta", "http://169.254.169.254/mcp")
        assert rows(setup.host) == []

    asyncio.run(main())


def test_failure_keeps_the_server_and_can_retry() -> None:
    async def main() -> None:
        reachable = False

        async with serve(demo_server()) as app_transport:

            class Flaky(ScriptedTransport):
                def request(self, request: Any) -> Any:
                    if reachable:
                        return app_transport.request(request)
                    return super().request(request)

            setup = Setup(Flaky(lambda r, m: None))
            await setup.lifecycle.start()
            with pytest.raises(MCPConnectionError):
                await setup.manager.add_server("Demo", URL, id="demo")
            assert setup.manager.servers_state().servers["demo"].state == "failed"
            reachable = True
            result = await setup.manager.connect_server("demo")
            assert result.state == "ready"

    asyncio.run(main())


def test_restore_after_eviction() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            first = Setup(transport)
            await first.lifecycle.start()
            await first.manager.add_server("Demo", URL, id="demo")

            # A new isolate: same storage, nothing in memory.
            fresh = AsgiTransport(transport.app)
            host = Host()
            host.ctx.storage = first.host.ctx.storage
            second = Setup(fresh, host=host)
            await second.lifecycle.start()
            assert "demo" in second.manager.connections
            await second.manager.wait_for_connections(timeout=5)
            assert second.manager.connections["demo"].state == MCPConnectionState.READY
            assert sorted(t.tool.name for t in second.manager.list_tools()) == [
                "add",
                "fail",
            ]
            # A 2026-07-28 session is adopted without negotiating again.
            methods = [json.loads(r.body or "{}").get("method") for r in fresh.requests]
            assert "server/discover" not in methods
            assert second.leases == second.released == 1

    asyncio.run(main())


def test_wait_for_connections_never_raises() -> None:
    async def main() -> None:
        setup = Setup(ScriptedTransport(lambda r, m: Reply(status=500)))
        await setup.lifecycle.start()
        await setup.manager.wait_for_connections(timeout=0)
        await setup.manager.wait_for_connections()

    asyncio.run(main())


def test_remove_and_filters() -> None:
    async def main() -> None:
        async with serve(demo_server()) as transport:
            setup = Setup(transport)
            await setup.lifecycle.start()
            await setup.manager.add_server("A", URL, id="a")
            await setup.manager.add_server("B", URL, id="b")
            assert {t.server_id for t in setup.manager.list_tools(server_id="a")} == {
                "a"
            }
            assert {
                t.server_id for t in setup.manager.list_tools(server_name=["B"])
            } == {"b"}
            assert setup.manager.list_tools(state=MCPConnectionState.FAILED) == []

            await setup.manager.remove_server("a")
            assert [row["id"] for row in rows(setup.host)] == ["b"]
            assert "a" not in setup.manager.connections
            with pytest.raises(MCPServerNotFoundError):
                await setup.manager.call_tool("a", "add")

    asyncio.run(main())
