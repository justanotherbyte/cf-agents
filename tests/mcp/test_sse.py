import asyncio
from collections.abc import AsyncIterator

import pytest

from agents.mcp.errors import MCPProtocolError
from agents.mcp.sse import MAX_EVENT_SIZE, SseEvent, iter_events


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


def decode(*parts: bytes) -> list[SseEvent]:
    async def collect() -> list[SseEvent]:
        return [event async for event in iter_events(_chunks(*parts))]

    return asyncio.run(collect())


@pytest.mark.parametrize("newline", [b"\n", b"\r", b"\r\n"])
def test_line_endings(newline: bytes) -> None:
    stream = newline.join([b"event: message", b"data: a", b"data: b", b"", b""])
    assert decode(stream) == [SseEvent(data="a\nb")]


def test_chunks_split_anywhere() -> None:
    stream = 'id: 7\ndata: {"x": "é"}\r\n\r\n'.encode()
    expected = [SseEvent(data='{"x": "é"}', id="7")]
    for cut in range(1, len(stream)):
        assert decode(stream[:cut], stream[cut:]) == expected


def test_comments_priming_and_fields() -> None:
    events = decode(
        b": keep-alive\n\n",
        b"id: 1\ndata:\n\n",  # a priming event: one empty data line
        b"retry: 3000\nevent: custom\ndata:no-space\n\n",
    )
    assert events == [
        SseEvent(data="", id="1"),
        SseEvent(event="custom", data="no-space", id="1", retry=3000),
    ]


def test_blank_lines_without_data_dispatch_nothing() -> None:
    assert decode(b"event: x\n\n\n") == []


def test_unterminated_event_is_dropped() -> None:
    assert decode(b"data: done\n\ndata: half") == [SseEvent(data="done")]


def test_bom_is_skipped() -> None:
    assert decode(b"\xef\xbb\xbfdata: x\n\n") == [SseEvent(data="x")]


def test_size_limit() -> None:
    line = b"data: " + b"x" * MAX_EVENT_SIZE + b"\n"
    with pytest.raises(MCPProtocolError):
        decode(line, b"\n")
