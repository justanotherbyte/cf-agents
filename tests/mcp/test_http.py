import asyncio
import json

from _servers import URL, demo_server, serve

from agents.mcp.http import HttpRequest, read_body


def test_asgi_round_trip() -> None:
    async def main() -> None:
        async with serve(demo_server(), json_response=True) as transport:
            request = HttpRequest(
                method="POST",
                url=URL,
                headers={
                    "accept": "application/json, text/event-stream",
                    "content-type": "application/json",
                },
                body=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {},
                            "clientInfo": {"name": "t", "version": "1"},
                        },
                    }
                ),
            )
            async with transport.request(request) as response:
                assert response.status == 200
                assert "mcp-session-id" in response.headers
                body = json.loads(await read_body(response))
            assert body["result"]["serverInfo"]["name"] == "Demo"

    asyncio.run(main())
