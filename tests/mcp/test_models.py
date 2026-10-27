import json

import pytest

from agents.core.encoding import to_json
from agents.mcp.models import (
    BlobResourceContents,
    CallToolResult,
    EmbeddedResource,
    GetPromptResult,
    ImageContent,
    Prompt,
    PromptArgument,
    ReadResourceResult,
    ResourceLink,
    ResourceTemplate,
    TextContent,
    TextResourceContents,
    Tool,
    UnknownContent,
)
from agents.mcp.types import MCPServerInfo, MCPServersState


def test_tool_ignores_unknown_keys() -> None:
    tool = Tool.from_wire(
        {
            "name": "add",
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True},
            "_meta": {"k": 1},
            "somethingNew": True,
        }
    )
    assert tool == Tool(
        name="add",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        annotations={"readOnlyHint": True},
        meta={"k": 1},
    )


def test_required_field_missing() -> None:
    with pytest.raises(KeyError):
        Tool.from_wire({"name": "x"})


def test_call_tool_result_content() -> None:
    result = CallToolResult.from_wire(
        {
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image", "data": "AA==", "mimeType": "image/png"},
                {"type": "resource_link", "uri": "file:///a", "name": "a"},
                {
                    "type": "resource",
                    "resource": {"uri": "file:///b", "blob": "AA=="},
                },
                {"type": "future", "x": 1},
            ],
            "structuredContent": {"sum": 3},
            "isError": True,
        }
    )
    assert result.content == [
        TextContent(text="hi"),
        ImageContent(data="AA==", mime_type="image/png"),
        ResourceLink(uri="file:///a", name="a"),
        EmbeddedResource(resource=BlobResourceContents(uri="file:///b", blob="AA==")),
        UnknownContent(fields={"type": "future", "x": 1}),
    ]
    assert result.structured_content == {"sum": 3}
    assert result.is_error
    assert CallToolResult.from_wire({}) == CallToolResult()


def test_prompt_and_results() -> None:
    prompt = Prompt.from_wire(
        {"name": "p", "arguments": [{"name": "q", "required": True}]}
    )
    assert prompt.arguments == [PromptArgument(name="q", required=True)]
    rendered = GetPromptResult.from_wire(
        {"messages": [{"role": "user", "content": {"type": "text", "text": "x"}}]}
    )
    assert rendered.messages[0].content == TextContent(text="x")
    contents = ReadResourceResult.from_wire(
        {"contents": [{"uri": "a://b", "text": "t", "mimeType": "text/plain"}]}
    )
    assert contents.contents == [
        TextResourceContents(uri="a://b", text="t", mime_type="text/plain")
    ]
    template = ResourceTemplate.from_wire({"uriTemplate": "a://{x}", "name": "t"})
    assert template.uri_template == "a://{x}"


def test_frame_shape() -> None:
    state = MCPServersState(
        servers={
            "a": MCPServerInfo(
                name="A",
                server_url="https://a",
                auth_url=None,
                state="ready",
                error=None,
                instructions=None,
                capabilities={"tools": {}},
            )
        },
        tools=[{"name": "t", "inputSchema": {}, "serverId": "a"}],
    )
    assert json.loads(to_json(state)) == {
        "servers": {
            "a": {
                "name": "A",
                "server_url": "https://a",
                "auth_url": None,
                "state": "ready",
                "error": None,
                "instructions": None,
                "capabilities": {"tools": {}},
            }
        },
        "tools": [{"name": "t", "inputSchema": {}, "serverId": "a"}],
        "prompts": [],
        "resources": [],
    }
