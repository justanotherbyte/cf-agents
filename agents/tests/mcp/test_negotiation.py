import asyncio
from typing import Any

import pytest
from _scripted import (
    INITIALIZED,
    MODERN_DISCOVER,
    Reply,
    ScriptedTransport,
    error,
    result,
)

from agents.mcp.errors import MCPProtocolError, MCPRpcError, MCPTransportError
from agents.mcp.http import HttpRequest
from agents.mcp.session import McpSession


def connect(answer: Any) -> tuple[McpSession, ScriptedTransport]:
    transport = ScriptedTransport(answer)
    session = McpSession(
        "https://mcp.example.com/mcp",
        http=transport,
        client_info={"name": "c", "version": "1"},
        headers={},
        auth=None,
        on_notification=lambda *_: None,
    )
    asyncio.run(session.connect())
    return session, transport


def handshake(request: HttpRequest, method: str | None) -> Reply:
    if method == "initialize":
        return result(request, INITIALIZED, **{"mcp-session-id": "sid"})
    return Reply(status=202)


def test_unsupported_version_reprobes_at_mutual() -> None:
    probes = 0

    def answer(request: HttpRequest, method: str | None) -> Reply:
        nonlocal probes
        probes += 1
        if probes == 1:
            return error(request, -32022, {"supported": ["2026-07-28", "2099-01-01"]})
        return result(request, MODERN_DISCOVER)

    session, transport = connect(answer)
    assert transport.methods == ["server/discover", "server/discover"]
    assert session.is_modern


def test_modern_only_and_disjoint_raises() -> None:
    def answer(request: HttpRequest, method: str | None) -> Reply:
        return error(request, -32022, {"supported": ["2099-01-01"]})

    with pytest.raises(MCPRpcError):
        connect(answer)


def test_other_rpc_errors_fall_back_to_initialize() -> None:
    def answer(request: HttpRequest, method: str | None) -> Reply:
        if method == "server/discover":
            return error(request, -32601)
        return handshake(request, method)

    session, transport = connect(answer)
    assert transport.methods == [
        "server/discover",
        "initialize",
        "notifications/initialized",
    ]
    assert session.session_id == "sid"
    assert "mcp-protocol-version" not in transport.requests[1].headers
    assert transport.requests[2].headers["mcp-session-id"] == "sid"


@pytest.mark.parametrize(
    "discover",
    [
        {"supportedVersions": ["2025-11-25"], "capabilities": {}},  # legacy advert
        {"supportedVersions": ["2026-07-28"]},  # unparseable
    ],
)
def test_non_modern_results_fall_back(discover: dict[str, Any]) -> None:
    def answer(request: HttpRequest, method: str | None) -> Reply:
        if method == "server/discover":
            return result(request, discover)
        return handshake(request, method)

    session, _ = connect(answer)
    assert session.protocol_version == "2025-11-25"


def test_initialize_answering_unsupported_reprobes() -> None:
    probes = 0

    def answer(request: HttpRequest, method: str | None) -> Reply:
        nonlocal probes
        if method == "server/discover":
            probes += 1
            if probes == 1:
                return Reply(status=404)
            return result(request, MODERN_DISCOVER)
        return error(request, -32022, {"supported": ["2026-07-28"]})

    session, transport = connect(answer)
    assert transport.methods == ["server/discover", "initialize", "server/discover"]
    assert session.is_modern


def test_network_errors_propagate() -> None:
    with pytest.raises(MCPTransportError):
        connect(lambda request, method: None)


def test_handshake_version_must_be_known() -> None:
    def answer(request: HttpRequest, method: str | None) -> Reply:
        if method == "server/discover":
            return Reply(status=404)
        return result(request, {**INITIALIZED, "protocolVersion": "1999-01-01"})

    with pytest.raises(MCPProtocolError):
        connect(answer)


def test_input_required_raises() -> None:
    def answer(request: HttpRequest, method: str | None) -> Reply:
        if method == "server/discover":
            return result(request, MODERN_DISCOVER)
        return result(request, {"resultType": "input_required"})

    session, _ = connect(answer)
    with pytest.raises(MCPProtocolError):
        asyncio.run(session.call_tool("x", {}))


def test_same_origin_redirect_is_followed() -> None:
    def answer(request: HttpRequest, method: str | None) -> Reply:
        if request.url.startswith("https://mcp.example.com/mcp/"):
            return result(request, MODERN_DISCOVER)
        if method == "server/discover" and request.url.endswith("/mcp"):
            return Reply(status=308, headers={"location": "/mcp/"})
        return Reply(status=302, headers={"location": "https://evil.example/"})

    session, transport = connect(answer)
    assert transport.requests[-1].url == "https://mcp.example.com/mcp/"
    assert session.is_modern
