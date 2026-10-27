"""MCP protocol records: what the catalog and the call methods return.

Dataclasses with snake_case attributes and ``wire()`` names for the
camelCase JSON (``.design/utilities.md`` §5). Field names follow
``mcp_types`` 2.3.0. Nested objects the client doesn't interpret
(``annotations``, ``icons``, ``_meta``) stay plain dicts, and unknown keys
are ignored, so a newer server never fails to decode.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from functools import cache
from typing import Any, ClassVar, Literal, NamedTuple, Self

from ..core.records import wire

__all__ = (
    "AudioContent",
    "BlobResourceContents",
    "CallToolResult",
    "ContentBlock",
    "EmbeddedResource",
    "GetPromptResult",
    "ImageContent",
    "Prompt",
    "PromptArgument",
    "PromptMessage",
    "ReadResourceResult",
    "Resource",
    "ResourceContents",
    "ResourceLink",
    "ResourceTemplate",
    "ServerPrompt",
    "ServerResource",
    "ServerResourceTemplate",
    "ServerTool",
    "TextContent",
    "TextResourceContents",
    "Tool",
    "UnknownContent",
)

type JsonObject = dict[str, Any]


class _Field(NamedTuple):
    name: str
    wire: str
    required: bool


@cache
def _fields(cls: Any) -> tuple[_Field, ...]:
    return tuple(
        _Field(
            name=f.name,
            wire=f.metadata.get("wire", f.name),
            required=f.default is dataclasses.MISSING
            and f.default_factory is dataclasses.MISSING,
        )
        for f in dataclasses.fields(cls)
    )


def _decode(cls: Any, payload: Mapping[str, Any], **nested: Any) -> dict[str, Any]:
    """Return ``cls``'s keyword arguments from ``payload``; ``nested`` overrides.

    Raises
    ------
    KeyError
        If a required field is missing.
    """
    kwargs: dict[str, Any] = {}
    for f in _fields(cls):
        if f.name in nested:
            kwargs[f.name] = nested[f.name]
        elif f.wire in payload:
            kwargs[f.name] = payload[f.wire]
        elif f.required:
            raise KeyError(f.wire)
    return kwargs


class _Record:
    """Decoding for records whose fields are all plain JSON values."""

    __slots__ = ()

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> Self:
        """Return the record for a JSON object.

        Raises
        ------
        KeyError
            If a required field is missing.
        """
        return cls(**_decode(cls, payload))


# Catalog entries


@dataclasses.dataclass(slots=True, kw_only=True)
class Tool(_Record):
    """A tool a server offers. ``input_schema`` is plain JSON Schema."""

    name: str
    input_schema: JsonObject = wire("inputSchema")
    title: str | None = None
    description: str | None = None
    output_schema: JsonObject | None = wire("outputSchema", None)
    annotations: JsonObject | None = None
    execution: JsonObject | None = None
    icons: list[JsonObject] | None = None
    meta: JsonObject | None = wire("_meta", None)


@dataclasses.dataclass(slots=True, kw_only=True)
class PromptArgument(_Record):
    """One argument a prompt takes."""

    name: str
    title: str | None = None
    description: str | None = None
    required: bool | None = None


@dataclasses.dataclass(slots=True, kw_only=True)
class Prompt(_Record):
    """A prompt template a server offers."""

    name: str
    title: str | None = None
    description: str | None = None
    arguments: list[PromptArgument] | None = None
    icons: list[JsonObject] | None = None
    meta: JsonObject | None = wire("_meta", None)

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> Self:
        """Return the prompt for a JSON object.

        Raises
        ------
        KeyError
            If a required field is missing.
        """
        arguments = payload.get("arguments")
        if arguments is not None:
            arguments = [PromptArgument.from_wire(item) for item in arguments]
        return cls(**_decode(cls, payload, arguments=arguments))


@dataclasses.dataclass(slots=True, kw_only=True)
class Resource(_Record):
    """A resource a server offers."""

    uri: str
    name: str
    title: str | None = None
    description: str | None = None
    mime_type: str | None = wire("mimeType", None)
    size: int | None = None
    annotations: JsonObject | None = None
    icons: list[JsonObject] | None = None
    meta: JsonObject | None = wire("_meta", None)


@dataclasses.dataclass(slots=True, kw_only=True)
class ResourceTemplate(_Record):
    """A parameterized resource (an RFC 6570 URI template)."""

    uri_template: str = wire("uriTemplate")
    name: str
    title: str | None = None
    description: str | None = None
    mime_type: str | None = wire("mimeType", None)
    annotations: JsonObject | None = None
    icons: list[JsonObject] | None = None
    meta: JsonObject | None = wire("_meta", None)


# Content


@dataclasses.dataclass(slots=True, kw_only=True)
class TextResourceContents(_Record):
    """A text resource's contents."""

    uri: str
    text: str
    mime_type: str | None = wire("mimeType", None)
    meta: JsonObject | None = wire("_meta", None)


@dataclasses.dataclass(slots=True, kw_only=True)
class BlobResourceContents(_Record):
    """A binary resource's contents, base64-encoded."""

    uri: str
    blob: str
    mime_type: str | None = wire("mimeType", None)
    meta: JsonObject | None = wire("_meta", None)


type ResourceContents = TextResourceContents | BlobResourceContents


def _resource_contents(payload: Mapping[str, Any]) -> ResourceContents:
    if "blob" in payload:
        return BlobResourceContents.from_wire(payload)
    return TextResourceContents.from_wire(payload)


@dataclasses.dataclass(slots=True, kw_only=True)
class TextContent(_Record):
    """Text in a tool result or prompt message."""

    text: str
    annotations: JsonObject | None = None
    meta: JsonObject | None = wire("_meta", None)
    type: ClassVar[str] = "text"


@dataclasses.dataclass(slots=True, kw_only=True)
class ImageContent(_Record):
    """A base64-encoded image."""

    data: str
    mime_type: str = wire("mimeType")
    annotations: JsonObject | None = None
    meta: JsonObject | None = wire("_meta", None)
    type: ClassVar[str] = "image"


@dataclasses.dataclass(slots=True, kw_only=True)
class AudioContent(_Record):
    """Base64-encoded audio."""

    data: str
    mime_type: str = wire("mimeType")
    annotations: JsonObject | None = None
    meta: JsonObject | None = wire("_meta", None)
    type: ClassVar[str] = "audio"


@dataclasses.dataclass(slots=True, kw_only=True)
class ResourceLink(_Record):
    """A link to a resource (not necessarily one ``resources/list`` returns)."""

    uri: str
    name: str
    title: str | None = None
    description: str | None = None
    mime_type: str | None = wire("mimeType", None)
    size: int | None = None
    annotations: JsonObject | None = None
    icons: list[JsonObject] | None = None
    meta: JsonObject | None = wire("_meta", None)
    type: ClassVar[str] = "resource_link"


@dataclasses.dataclass(slots=True, kw_only=True)
class EmbeddedResource(_Record):
    """A resource's contents embedded in a result."""

    resource: ResourceContents
    annotations: JsonObject | None = None
    meta: JsonObject | None = wire("_meta", None)
    type: ClassVar[str] = "resource"

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> Self:
        """Return the embedded resource for a JSON object.

        Raises
        ------
        KeyError
            If a required field is missing.
        """
        resource = _resource_contents(payload["resource"])
        return cls(**_decode(cls, payload, resource=resource))


@dataclasses.dataclass(slots=True, kw_only=True)
class UnknownContent:
    """A content block of a type this client doesn't know, kept as sent."""

    fields: JsonObject

    @property
    def type(self) -> str | None:
        """``str | None``: the block's ``type``."""
        return self.fields.get("type")


type ContentBlock = (
    TextContent
    | ImageContent
    | AudioContent
    | ResourceLink
    | EmbeddedResource
    | UnknownContent
)

_CONTENT: Mapping[str, Any] = {
    cls.type: cls
    for cls in (TextContent, ImageContent, AudioContent, ResourceLink, EmbeddedResource)
}


def _content(payload: Mapping[str, Any]) -> ContentBlock:
    cls = _CONTENT.get(payload.get("type", ""))
    if cls is None:
        return UnknownContent(fields=dict(payload))
    return cls.from_wire(payload)


def _contents(payload: Sequence[Mapping[str, Any]]) -> list[ContentBlock]:
    return [_content(item) for item in payload]


# Results


@dataclasses.dataclass(slots=True, kw_only=True)
class CallToolResult(_Record):
    """What a tool call returned. ``is_error`` is a tool error, not an exception."""

    content: list[ContentBlock] = dataclasses.field(default_factory=list)
    structured_content: Any = wire("structuredContent", None)
    is_error: bool = wire("isError", False)
    meta: JsonObject | None = wire("_meta", None)

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> Self:
        """Return the result for a JSON object."""
        content = _contents(payload.get("content", []))
        return cls(**_decode(cls, payload, content=content))


@dataclasses.dataclass(slots=True, kw_only=True)
class PromptMessage:
    """One message of a rendered prompt."""

    role: Literal["user", "assistant"]
    content: ContentBlock


@dataclasses.dataclass(slots=True, kw_only=True)
class GetPromptResult(_Record):
    """A rendered prompt."""

    messages: list[PromptMessage]
    description: str | None = None
    meta: JsonObject | None = wire("_meta", None)

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> Self:
        """Return the result for a JSON object.

        Raises
        ------
        KeyError
            If a required field is missing.
        """
        messages = [
            PromptMessage(role=item["role"], content=_content(item["content"]))
            for item in payload["messages"]
        ]
        return cls(**_decode(cls, payload, messages=messages))


@dataclasses.dataclass(slots=True, kw_only=True)
class ReadResourceResult(_Record):
    """A resource's contents."""

    contents: list[ResourceContents]
    meta: JsonObject | None = wire("_meta", None)

    @classmethod
    def from_wire(cls, payload: Mapping[str, Any]) -> Self:
        """Return the result for a JSON object.

        Raises
        ------
        KeyError
            If a required field is missing.
        """
        contents = [_resource_contents(item) for item in payload["contents"]]
        return cls(**_decode(cls, payload, contents=contents))


# Catalog entries with their server


@dataclasses.dataclass(slots=True, kw_only=True)
class ServerTool:
    """A tool and the server it belongs to."""

    server_id: str
    tool: Tool


@dataclasses.dataclass(slots=True, kw_only=True)
class ServerPrompt:
    """A prompt and the server it belongs to."""

    server_id: str
    prompt: Prompt


@dataclasses.dataclass(slots=True, kw_only=True)
class ServerResource:
    """A resource and the server it belongs to."""

    server_id: str
    resource: Resource


@dataclasses.dataclass(slots=True, kw_only=True)
class ServerResourceTemplate:
    """A resource template and the server it belongs to."""

    server_id: str
    resource_template: ResourceTemplate
