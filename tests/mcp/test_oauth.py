"""The OAuth flow against the MCP SDK's authorization server."""

import asyncio
import json
from http import HTTPMethod
from typing import Any
from urllib.parse import parse_qs, urlsplit

from _auth_server import InMemoryAuthProvider, oauth_server
from _servers import URL, serve
from test_manager import Setup
from workers import Request

from agents.mcp.http import HttpRequest
from agents.mcp.models import TextContent
from agents.mcp.types import MCPConnectionState

CALLBACK = "https://agent.example.com/agents/test-agent/a/callback"


async def authorize(transport: Any, auth_url: str) -> str:
    """Play the user: follow the authorization URL, return the callback URL."""
    async with transport.request(HttpRequest(method="GET", url=auth_url)) as response:
        assert response.status == 302, response.status
        return response.headers.get("location")


def test_authorize_callback_and_call() -> None:
    async def main() -> None:
        provider = InMemoryAuthProvider()
        async with serve(oauth_server(provider)) as transport:
            setup = Setup(transport)
            await setup.lifecycle.start()
            result = await setup.manager.add_server(
                "Secured", URL, id="sec", callback_url=CALLBACK
            )
            assert result.state == "authenticating"
            assert result.auth_url is not None
            query = parse_qs(urlsplit(result.auth_url).query)
            assert query["code_challenge_method"] == ["S256"]
            assert query["redirect_uri"] == [CALLBACK]
            assert query["resource"] == [URL]
            assert query["state"][0].endswith(".sec")
            assert (
                setup.manager.servers_state().servers["sec"].auth_url == result.auth_url
            )
            assert "mcp:client:authorize" in setup.event_types()

            callback = await authorize(transport, result.auth_url)
            assert callback.startswith(CALLBACK)
            response = await setup.manager.on_request(Request(callback))
            assert response is not None
            assert response.status == 302
            assert response.headers.get("location") == "https://agent.example.com"
            await setup.manager.wait_for_connections(timeout=5)
            assert setup.manager.connections["sec"].state == MCPConnectionState.READY
            assert setup.manager.servers_state().servers["sec"].auth_url is None

            called = await setup.manager.call_tool("sec", "add", {"a": 2, "b": 3})
            assert called.content == [TextContent(text="5")]
            assert provider.authorize_params[0].resource == URL

            # The tokens are stored under upstream's keys.
            kv = setup.host.ctx.storage.kv
            client_id = next(iter(provider.clients))
            assert f"/TestAgent/sec/{client_id}/token" in kv
            assert f"/TestAgent/sec/{client_id}/client_info/" in kv
            assert not any("/state/" in key or "code_verifier" in key for key in kv)

            # A replayed (stale) callback is a no-op success.
            again = await setup.manager.on_request(Request(callback))
            assert again is not None and again.status == 302

    asyncio.run(main())


def test_tokens_survive_eviction_and_refresh() -> None:
    async def main() -> None:
        provider = InMemoryAuthProvider()
        async with serve(oauth_server(provider)) as transport:
            first = Setup(transport)
            await first.lifecycle.start()
            result = await first.manager.add_server(
                "Secured", URL, id="sec", callback_url=CALLBACK
            )
            assert result.auth_url is not None
            await first.manager.on_request(
                Request(await authorize(transport, result.auth_url))
            )
            await first.manager.wait_for_connections(timeout=5)

            await first.manager.dispose()  # evicted
            provider.access.clear()  # every access token expired
            second = Setup(transport, host=first.host)
            await second.lifecycle.start()
            await second.manager.wait_for_connections(timeout=5)
            assert second.manager.connections["sec"].state == MCPConnectionState.READY
            assert provider.refreshes == 1

    asyncio.run(main())


def test_callback_with_error_fails_the_connection() -> None:
    async def main() -> None:
        provider = InMemoryAuthProvider()
        async with serve(oauth_server(provider)) as transport:
            setup = Setup(transport)
            await setup.lifecycle.start()
            result = await setup.manager.add_server(
                "Secured", URL, id="sec", callback_url=CALLBACK
            )
            assert result.auth_url is not None
            state = parse_qs(urlsplit(result.auth_url).query)["state"][0]
            denied = (
                f"{CALLBACK}?state={state}&error=access_denied&error_description=No"
            )
            response = await setup.manager.on_request(Request(denied))
            assert response is not None
            assert response.status == 302
            connection = setup.manager.connections["sec"]
            assert connection.state == MCPConnectionState.FAILED
            assert connection.error == "No"
            assert not any("code_verifier" in key for key in setup.host.ctx.storage.kv)

    asyncio.run(main())


def test_callbacks_that_dont_match_pass_through() -> None:
    async def main() -> None:
        provider = InMemoryAuthProvider()
        async with serve(oauth_server(provider)) as transport:
            setup = Setup(transport)
            await setup.lifecycle.start()
            await setup.manager.add_server(
                "Secured", URL, id="sec", callback_url=CALLBACK
            )
            manager = setup.manager
            assert (
                await manager.on_request(Request(f"{CALLBACK}?state=x.unknown")) is None
            )
            assert await manager.on_request(Request(f"{CALLBACK}?state=nodot")) is None
            assert (
                await manager.on_request(
                    Request("https://agent.example.com/other?state=a.sec")
                )
                is None
            )
            post = Request(f"{CALLBACK}?state=a.sec&code=c", method=HTTPMethod.POST)
            assert await manager.on_request(post) is None

    asyncio.run(main())


def test_forged_state_is_ignored() -> None:
    async def main() -> None:
        provider = InMemoryAuthProvider()
        async with serve(oauth_server(provider)) as transport:
            setup = Setup(transport)
            await setup.lifecycle.start()
            await setup.manager.add_server(
                "Secured", URL, id="sec", callback_url=CALLBACK
            )
            forged = f"{CALLBACK}?state=forged.sec&code=c"
            response = await setup.manager.on_request(Request(forged))
            assert response is not None
            # Someone else's callback can't fail this connection.
            connection = setup.manager.connections["sec"]
            assert connection.state == MCPConnectionState.AUTHENTICATING

    asyncio.run(main())


def test_redirect_options_and_hook() -> None:
    async def main() -> None:
        provider = InMemoryAuthProvider()
        async with serve(oauth_server(provider)) as transport:
            setup = Setup(transport)
            setup.manager._error_redirect = "/oauth/error?x=1"
            await setup.lifecycle.start()
            result = await setup.manager.add_server(
                "Secured", URL, id="sec", callback_url=CALLBACK
            )
            assert result.auth_url is not None
            state = parse_qs(urlsplit(result.auth_url).query)["state"][0]
            denied = f"{CALLBACK}?state={state}&error=access_denied"
            response = await setup.manager.on_request(Request(denied))
            assert response is not None
            assert response.headers.get("location") == (
                "https://agent.example.com/oauth/error?x=1&error=access_denied"
            )
            seen = []

            async def hook(outcome: Any, request: Any) -> Any:
                seen.append(outcome)
                return None

            setup.manager._on_oauth_callback = hook
            await setup.manager.on_request(Request(denied))
            assert seen and seen[0].server_id == "sec"
            json.dumps(seen[0].error)

    asyncio.run(main())
