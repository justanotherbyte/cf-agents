"""agents.mcp.server: the stateless Workers handler, driven with fake requests."""

import asyncio
import importlib
import json
import sys
from http import HTTPMethod
from typing import Any

import pytest
from fake_runtime import FakeCtx
from mcp.server import MCPServer, Server
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from workers import Request, Response

from agents import Agent, get_current_agent
from agents.mcp.errors import MCPError
from agents.mcp.server import (
    CORSOptions,
    McpAuthContext,
    McpHandler,
    MCPServerConfigError,
    asgi,
    create_mcp_handler,
    get_mcp_auth_context,
)

MODERN = "2026-07-28"
HANDSHAKE = "2025-11-25"
URL = "https://example.com/mcp"


class BodyRequest(Request):
    """The fake Workers request, plus the body ``bytes()`` the handler reads."""

    def __init__(
        self,
        url: str = URL,
        *,
        method: HTTPMethod = HTTPMethod.POST,
        headers: dict[str, str] | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(url, method=method, headers=headers)
        self._body = b"" if body is None else json.dumps(body).encode()

    async def bytes(self) -> bytes:
        return self._body


def build_server() -> MCPServer:
    server = MCPServer("Test", version="1.0.0")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @server.tool()
    async def count(n: int, ctx: Context) -> int:
        """Report progress, then return ``n``."""
        await ctx.report_progress(1, n)
        return n

    @server.tool()
    async def hang(ctx: Context) -> int:
        """Report progress, then never finish."""
        await ctx.report_progress(1)
        await asyncio.Event().wait()
        return 0

    @server.tool()
    def whoami() -> str:
        """Return the auth context's user."""
        context = get_mcp_auth_context()
        return "anonymous" if context is None else context.props["user"]

    @server.tool()
    def agent_class() -> str:
        """Return the class of the agent serving the request."""
        current = get_current_agent()
        return "none" if current is None else type(current.agent).__name__

    return server


def modern_request(
    method: str,
    params: dict[str, Any],
    *,
    url: str = URL,
    headers: dict[str, str] | None = None,
    progress_token: str | None = None,
) -> BodyRequest:
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": MODERN,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    if progress_token is not None:
        meta["progressToken"] = progress_token
    routing = {"MCP-Protocol-Version": MODERN, "Mcp-Method": method}
    if "name" in params:
        routing["Mcp-Name"] = params["name"]
    return BodyRequest(
        url,
        headers={**_post_headers(), **routing, **(headers or {})},
        body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {**params, "_meta": meta},
        },
    )


def handshake_request(
    method: str, params: dict[str, Any], *, version: str | None = HANDSHAKE
) -> BodyRequest:
    headers = _post_headers()
    if version is not None:
        headers["MCP-Protocol-Version"] = version
    return BodyRequest(
        headers=headers,
        body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"name": name, "arguments": arguments}


def _post_headers() -> dict[str, str]:
    return {
        "Host": "example.com",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def content_type(response: Response) -> str:
    return response.headers.get("content-type") or ""


async def read_body(response: Response) -> str:
    body = response.body
    if body is None:
        return ""
    if isinstance(body, bytes):
        return body.decode()
    if isinstance(body, str):
        return body
    return "".join([piece async for piece in body])


async def messages(response: Response) -> list[dict[str, Any]]:
    """Return the JSON-RPC messages in a JSON or SSE body."""
    text = await read_body(response)
    if not content_type(response).startswith("text/event-stream"):
        return [json.loads(text)]
    return [
        json.loads(line.removeprefix("data:"))
        for line in text.splitlines()
        if line.startswith("data:") and line.removeprefix("data:").strip()
    ]


def serve(handler: McpHandler, request: Request) -> Response:
    return asyncio.run(handler(request, None, None))


def serve_messages(handler: McpHandler, request: Request) -> list[dict[str, Any]]:
    async def scenario() -> list[dict[str, Any]]:
        return await messages(await handler(request, None, None))

    return asyncio.run(scenario())


def tool_text(message: dict[str, Any]) -> str:
    return message["result"]["content"][0]["text"]


# Both eras


@pytest.mark.parametrize("json_response", [True, False])
def test_modern_tools_call_round_trip(json_response: bool) -> None:
    handler = create_mcp_handler(build_server(), json_response=json_response)
    response = serve(handler, modern_request("tools/call", call("add", a=2, b=3)))

    assert response.status == 200
    # A result sent without notifications first is JSON even in SSE mode.
    assert content_type(response) == "application/json"
    [message] = asyncio.run(messages(response))
    assert message["id"] == 1
    assert tool_text(message) == "5"


def test_modern_sse_streams_notifications_before_the_result() -> None:
    handler = create_mcp_handler(build_server())
    request = modern_request("tools/call", call("count", n=3), progress_token="p1")

    async def scenario() -> tuple[Response, list[dict[str, Any]]]:
        response = await handler(request, None, None)
        return response, await messages(response)

    response, [progress, result] = asyncio.run(scenario())
    assert content_type(response).startswith("text/event-stream")
    assert progress["method"] == "notifications/progress"
    assert progress["params"]["progressToken"] == "p1"
    assert tool_text(result) == "3"


def test_client_leaving_mid_stream_stops_the_request() -> None:
    handler = create_mcp_handler(build_server())
    request = modern_request("tools/call", call("hang"), progress_token="p1")

    async def scenario() -> tuple[str, int]:
        response = await handler(request, None, None)
        first = await anext(response.body)
        await response.body.aclose()
        await asyncio.sleep(0.05)
        return first, len(asgi._running)

    first, running = asyncio.run(scenario())
    assert "notifications/progress" in first
    assert running == 0


def test_app_failing_mid_stream_ends_the_stream_with_its_error() -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        raise ValueError("boom")

    async def scenario() -> str:
        response = await asgi.serve(app, BodyRequest())
        first = await anext(response.body)
        with pytest.raises(ValueError, match="boom"):
            await asyncio.wait_for(anext(response.body), 1)
        return first

    assert asyncio.run(scenario()) == "a"


def test_modern_json_mode_answers_with_the_result_only() -> None:
    handler = create_mcp_handler(build_server(), json_response=True)
    request = modern_request("tools/call", call("count", n=3), progress_token="p1")

    [message] = serve_messages(handler, request)
    assert tool_text(message) == "3"


@pytest.mark.parametrize("json_response", [True, False])
def test_handshake_era_initialize_then_tools_call(json_response: bool) -> None:
    handler = create_mcp_handler(build_server(), json_response=json_response)
    initialize = handshake_request(
        "initialize",
        {
            "protocolVersion": HANDSHAKE,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
        version=None,
    )

    [init] = serve_messages(handler, initialize)
    assert init["result"]["protocolVersion"] == HANDSHAKE
    assert init["result"]["capabilities"]["resources"]["subscribe"] is False
    assert serve(handler, initialize).headers.get("mcp-session-id") is None

    response = serve(handler, handshake_request("tools/call", call("add", a=1, b=2)))
    expected = "application/json" if json_response else "text/event-stream"
    assert content_type(response).startswith(expected)
    [result] = asyncio.run(messages(response))
    assert tool_text(result) == "3"


def test_shared_server_serves_many_requests() -> None:
    handler = create_mcp_handler(build_server())

    for a in range(3):
        [message] = serve_messages(
            handler, modern_request("tools/call", call("add", a=a, b=1))
        )
        assert tool_text(message) == str(a + 1)


def test_low_level_server_is_served() -> None:
    server = Server("low-level", version="1")
    handler = create_mcp_handler(server)
    request = modern_request("server/discover", {})

    [message] = serve_messages(handler, request)
    assert MODERN in message["result"]["supportedVersions"]


# Routing and CORS


def test_other_paths_get_404_with_cors() -> None:
    handler = create_mcp_handler(build_server())
    response = serve(handler, BodyRequest("https://example.com/other"))

    assert response.status == 404
    assert response.headers.get("access-control-allow-origin") == "*"


def test_custom_route() -> None:
    handler = create_mcp_handler(build_server(), route="/custom")

    assert serve(handler, modern_request("tools/list", {})).status == 404
    request = modern_request("tools/list", {}, url="https://example.com/custom")
    [message] = serve_messages(handler, request)
    assert {tool["name"] for tool in message["result"]["tools"]} >= {"add", "count"}


def test_cors_preflight() -> None:
    handler = create_mcp_handler(
        build_server(),
        cors=CORSOptions(origin="https://client.example", max_age=60),
    )
    response = serve(
        handler,
        BodyRequest(
            method=HTTPMethod.OPTIONS,
            headers={"Host": "example.com", "Origin": "https://client.example"},
        ),
    )

    assert response.status == 200
    assert response.headers.get("access-control-allow-origin") == (
        "https://client.example"
    )
    assert response.headers.get("access-control-max-age") == "60"
    allowed = response.headers.get("access-control-allow-headers") or ""
    assert {"mcp-method", "mcp-name"} <= set(allowed.lower().split(", "))


def test_cors_headers_on_mcp_responses() -> None:
    handler = create_mcp_handler(build_server())
    response = serve(handler, modern_request("tools/list", {}))

    assert response.headers.get("access-control-allow-origin") == "*"
    assert response.headers.get("access-control-expose-headers") == "mcp-session-id"


def test_cors_none_adds_no_headers_and_passes_options_through() -> None:
    handler = create_mcp_handler(build_server(), cors=None)

    assert (
        serve(handler, modern_request("tools/list", {})).headers.get(
            "access-control-allow-origin"
        )
        is None
    )
    preflight = serve(handler, BodyRequest(method=HTTPMethod.OPTIONS))
    assert preflight.status != 200
    assert preflight.headers.get("access-control-allow-origin") is None


# Host and Origin


def rejected(response: Response, what: str) -> bool:
    body = json.loads(response.body)
    return (
        response.status == 403
        and body["id"] is None
        and body["error"]["code"] == -32000
        and body["error"]["message"].startswith(f"Invalid {what} header")
    )


def test_requests_without_origin_are_allowed() -> None:
    handler = create_mcp_handler(build_server())
    assert serve(handler, modern_request("tools/list", {})).status == 200


def test_custom_domain_rejects_a_browser_origin_by_default() -> None:
    handler = create_mcp_handler(build_server())
    request = modern_request("tools/list", {}, headers={"Origin": "https://evil.com"})

    response = serve(handler, request)
    assert rejected(response, "Origin")
    assert response.headers.get("access-control-allow-origin") == "*"


@pytest.mark.parametrize("origin", ["null", "not a url", "ftp://localhost"])
def test_opaque_or_malformed_origin_is_rejected(origin: str) -> None:
    handler = create_mcp_handler(build_server())
    request = modern_request("tools/list", {}, headers={"Origin": origin})
    assert rejected(serve(handler, request), "Origin")


def test_concrete_cors_origin_is_allowed_on_any_port() -> None:
    handler = create_mcp_handler(
        build_server(), cors=CORSOptions(origin="https://app.example.com")
    )
    request = modern_request(
        "tools/list", {}, headers={"Origin": "https://app.example.com:8443"}
    )
    assert serve(handler, request).status == 200


def test_explicit_origin_allowlist() -> None:
    handler = create_mcp_handler(
        build_server(), allowed_origin_hostnames=["Client.Example"]
    )
    allowed = modern_request(
        "tools/list", {}, headers={"Origin": "https://client.example"}
    )
    localhost = modern_request(
        "tools/list", {}, headers={"Origin": "http://localhost:3000"}
    )

    assert serve(handler, allowed).status == 200
    assert rejected(serve(handler, localhost), "Origin")


def test_origin_check_can_be_turned_off() -> None:
    handler = create_mcp_handler(build_server(), allowed_origin_hostnames="*")
    request = modern_request("tools/list", {}, headers={"Origin": "null"})
    assert serve(handler, request).status == 200


def test_localhost_endpoint_checks_host_and_origin() -> None:
    handler = create_mcp_handler(build_server())
    url = "http://localhost:8787/mcp"

    def request(host: str, origin: str) -> BodyRequest:
        return modern_request(
            "tools/list", {}, url=url, headers={"Host": host, "Origin": origin}
        )

    assert (
        serve(handler, request("localhost:8787", "http://localhost:3000")).status == 200
    )
    assert rejected(serve(handler, request("localhost", "https://evil.com")), "Origin")
    assert rejected(serve(handler, request("evil.com", "http://localhost")), "Host")
    assert rejected(
        serve(handler, request("evil.com@localhost", "http://localhost")), "Host"
    )


def test_workers_dev_endpoint_accepts_only_its_own_host_and_origin() -> None:
    handler = create_mcp_handler(build_server())
    host = "server.account.workers.dev"

    def request(host_header: str, origin: str) -> BodyRequest:
        return modern_request(
            "tools/list",
            {},
            url=f"https://{host}/mcp",
            headers={"Host": host_header, "Origin": origin},
        )

    assert serve(handler, request(host, f"https://{host}")).status == 200
    assert rejected(serve(handler, request("evil.com", f"https://{host}")), "Host")
    assert rejected(
        serve(handler, request(host, "https://other.account.workers.dev")), "Origin"
    )


def test_explicit_host_allowlist() -> None:
    handler = create_mcp_handler(build_server(), allowed_hostnames=["mcp.example.com"])

    assert rejected(serve(handler, modern_request("tools/list", {})), "Host")
    request = modern_request("tools/list", {}, headers={"Host": "mcp.example.com"})
    assert serve(handler, request).status == 200


def test_host_and_origin_are_checked_before_the_preflight() -> None:
    handler = create_mcp_handler(build_server())
    request = BodyRequest(
        method=HTTPMethod.OPTIONS,
        headers={"Host": "example.com", "Origin": "https://evil.com"},
    )
    assert rejected(serve(handler, request), "Origin")


# Factories


def test_factory_is_called_once_per_request() -> None:
    built: list[MCPServer] = []

    def factory() -> MCPServer:
        built.append(build_server())
        return built[-1]

    handler = create_mcp_handler(factory)
    assert built == []
    for _ in range(2):
        serve(handler, modern_request("tools/call", call("add", a=1, b=1)))
    assert len(built) == 2 and built[0] is not built[1]


def test_rejected_requests_build_no_server() -> None:
    built: list[MCPServer] = []
    handler = create_mcp_handler(lambda: built.append(build_server()) or built[-1])

    serve(handler, BodyRequest("https://example.com/other"))
    serve(
        handler,
        modern_request("tools/list", {}, headers={"Origin": "https://evil.com"}),
    )
    assert built == []


def test_failing_factory_is_a_500() -> None:
    def factory() -> MCPServer:
        raise RuntimeError("no database")

    response = serve(create_mcp_handler(factory), modern_request("tools/list", {}))
    body = json.loads(response.body)
    assert response.status == 500
    assert body["error"] == {"code": -32603, "message": "Internal server error"}
    assert response.headers.get("access-control-allow-origin") == "*"


# Server checks


class _Verifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        return None


def oauth_server() -> MCPServer:
    return MCPServer(
        "OAuth",
        token_verifier=_Verifier(),
        auth=AuthSettings(
            issuer_url="https://auth.example.com",
            resource_server_url="https://example.com/mcp",
            validate_token_resource=False,
        ),
    )


def test_sdk_auth_settings_are_refused() -> None:
    with pytest.raises(MCPServerConfigError, match="auth_context") as caught:
        create_mcp_handler(oauth_server())
    assert isinstance(caught.value, MCPError)


def test_sdk_auth_settings_from_a_factory_are_refused() -> None:
    handler = create_mcp_handler(oauth_server)
    with pytest.raises(MCPServerConfigError):
        serve(handler, modern_request("tools/list", {}))


def test_subscriptions_are_off() -> None:
    handler = create_mcp_handler(build_server())

    [discover] = serve_messages(handler, modern_request("server/discover", {}))
    assert discover["result"]["capabilities"]["tools"].get("listChanged") is not True
    [listen] = serve_messages(
        handler,
        modern_request(
            "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
        ),
    )
    assert listen["error"]["code"] == -32601


# Auth context and the current agent


def test_auth_context_reaches_tools() -> None:
    handler = create_mcp_handler(
        build_server(), auth_context=McpAuthContext(props={"user": "ada"})
    )
    request = modern_request("tools/call", call("whoami"))

    [message] = serve_messages(handler, request)
    assert tool_text(message) == "ada"
    assert get_mcp_auth_context() is None


def test_auth_context_reaches_streamed_tools() -> None:
    handler = create_mcp_handler(
        build_server(), auth_context=McpAuthContext(props={"user": "ada"})
    )
    request = handshake_request("tools/call", call("whoami"))

    [message] = serve_messages(handler, request)
    assert tool_text(message) == "ada"


def test_no_auth_context() -> None:
    [message] = serve_messages(
        create_mcp_handler(build_server()), modern_request("tools/call", call("whoami"))
    )
    assert tool_text(message) == "anonymous"


handler_for_agent = create_mcp_handler(build_server())


class McpAgent(Agent):
    async def on_request(self, request: Request) -> Response:
        return await handler_for_agent.fetch(request)


def test_tools_called_from_an_agent_see_the_current_agent() -> None:
    agent: Any = McpAgent(FakeCtx("room-1"), env=None)  # fetch comes from the runtime

    async def scenario() -> list[dict[str, Any]]:
        response = await agent.fetch(modern_request("tools/call", call("agent_class")))
        return await messages(response)

    [message] = asyncio.run(scenario())
    assert tool_text(message) == "McpAgent"

    [outside] = serve_messages(
        handler_for_agent, modern_request("tools/call", call("agent_class"))
    )
    assert tool_text(outside) == "none"


# Import guard


def test_import_without_the_sdk_names_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in [name for name in sys.modules if name.startswith("agents.mcp.server")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "mcp", None)

    with pytest.raises(ImportError, match=r"cf-agents\[mcp-server\].*3\.14"):
        importlib.import_module("agents.mcp.server")
