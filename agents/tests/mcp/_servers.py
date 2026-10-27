"""MCP SDK servers the client's contract tests talk to."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from _asgi_transport import AsgiTransport
from mcp.server.mcpserver import MCPServer

URL = "http://127.0.0.1:8000/mcp"


def demo_server() -> MCPServer:
    server = MCPServer("Demo", version="1.0.0", instructions="Be nice.")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @server.tool()
    def fail() -> str:
        """Always fails."""
        raise ValueError("nope")

    @server.prompt()
    def greet(name: str) -> str:
        """Greet someone."""
        return f"Hello, {name}!"

    @server.resource("note://readme")
    def readme() -> str:
        """The readme."""
        return "read me"

    @server.resource("note://{id}")
    def note(id: str) -> str:
        """A note."""
        return f"note {id}"

    return server


@asynccontextmanager
async def serve(server: MCPServer, **app_options: Any) -> AsyncGenerator[AsgiTransport]:
    app = server.streamable_http_app(**app_options)
    async with app.router.lifespan_context(app):
        yield AsgiTransport(app)
