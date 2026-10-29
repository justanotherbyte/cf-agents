import asyncio
import struct
from datetime import UTC, datetime

import pytest
from test_setup import Container, Stream, data, errno_frame, frame, success

from sandboxes import (
    Files,
    SandboxAbortError,
    SandboxDirectoryEntry,
    SandboxFileError,
    SandboxProtocolError,
)

SHIM = "/usr/local/bin/sandbox-shim"


def split(payload: bytes) -> list[bytes]:
    """Deliver bytes one at a time, to exercise frame reassembly."""
    return [payload[i : i + 1] for i in range(len(payload))]


async def test_mkdir_runs_the_shim_with_flags_and_resolved_path() -> None:
    container = Container()
    container.script(stdout=[success()])

    await Files(container).mkdir("a/b", recursive=True, cwd="/work", user="1000:1000")

    cmd, options = container.calls[0]
    assert cmd == [SHIM, "mkdir", "/work/a/b", "--recursive"]
    assert options == {"stdout": "pipe", "stderr": "ignore", "user": "1000:1000"}


async def test_remove_passes_both_flags() -> None:
    container = Container()
    container.script(stdout=[success()])

    await Files(container).remove("/x", recursive=True, force=True)

    assert container.calls[0][0] == [SHIM, "remove", "/x", "--recursive", "--force"]


async def test_rename_error_carries_both_paths() -> None:
    container = Container()
    container.script(stdout=[errno_frame(18, "cross-device link")])

    with pytest.raises(SandboxFileError) as caught:
        await Files(container).rename("/a", "b", cwd="/c")

    error = caught.value
    assert container.calls[0][0] == [SHIM, "rename", "/a", "/c/b"]
    assert (error.code, error.operation, error.path, error.destination) == (
        "EXDEV",
        "rename",
        "/a",
        "b",
    )
    assert str(error) == "rename '/a' to 'b': cross-device link"


async def test_errno_without_detail_uses_the_code_and_unknown_errno() -> None:
    container = Container()
    container.script(stdout=[errno_frame(2)])
    container.script(stdout=[errno_frame(999, "odd")])
    files = Files(container)

    with pytest.raises(SandboxFileError) as missing:
        await files.mkdir("/x")
    with pytest.raises(SandboxFileError) as unknown:
        await files.mkdir("/x")

    assert (missing.value.code, missing.value.detail) == ("ENOENT", "ENOENT")
    assert unknown.value.code == "UNKNOWN"


async def test_stat_decodes_the_payload() -> None:
    payload = struct.pack("<BQIIIqqq", 1, 2**40, 0o40755, 1, 2, 0, 1_500, -1_000)
    container = Container()
    container.script(stdout=split(data(payload)))

    stat = await Files(container).stat("/d")

    assert stat.type == "directory"
    assert stat.size == 2**40
    assert (stat.mode, stat.uid, stat.gid) == (0o40755, 1, 2)
    assert stat.accessed_at == datetime(1970, 1, 1, tzinfo=UTC)
    assert stat.modified_at == datetime(1970, 1, 1, 0, 0, 1, 500_000, tzinfo=UTC)
    assert stat.changed_at.year == 1969


async def test_read_directory_keeps_a_leading_bom() -> None:
    name = "﻿notes".encode()
    payload = (
        struct.pack("<I", 2)
        + struct.pack("<BH", 0, len(name))
        + name
        + struct.pack("<BH", 4, 3)
        + b"tty"
    )
    container = Container()
    container.script(stdout=[data(payload)])

    entries = await Files(container).read_directory("/d")

    assert container.calls[0][0][1] == "read-directory"
    assert entries == [
        SandboxDirectoryEntry("﻿notes", "file"),
        SandboxDirectoryEntry("tty", "character_device"),
    ]


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ([b"XXXX" + frame(0)[4:]], "invalid protocol magic"),
        ([frame(0)[:4] + b"\x02" + frame(0)[5:]], "protocol 2 is not supported"),
        ([frame(7)], "unknown control status 7"),
        ([frame(0)[:5]], "truncated control data"),
        ([success() + b"x"], "trailing control data"),
        ([success(), b"x"], "trailing control data"),
        ([data(b"x")], "did not confirm command completion"),
        ([errno_frame(0)], "invalid errno 0"),
        ([frame(1, b"\x02\x00\x00\x00\xff")], "non-UTF-8 error message"),
    ],
)
async def test_protocol_violations(stdout: list[bytes], message: str) -> None:
    container = Container()
    process = container.script(stdout=stdout)

    with pytest.raises(SandboxProtocolError, match=message):
        await Files(container).mkdir("/x")
    assert process.killed == 9


async def test_nonzero_exit_after_success_is_a_protocol_error() -> None:
    container = Container()
    container.script(stdout=[success()], exit_code=3)

    with pytest.raises(SandboxProtocolError, match="exited with code 3"):
        await Files(container).mkdir("/x")


@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda f: f.mkdir(""), ValueError),
        (lambda f: f.mkdir("/a\0b"), ValueError),
        (lambda f: f.mkdir("relative"), ValueError),
        (lambda f: f.mkdir(3), TypeError),
        (lambda f: f.mkdir("/x", cwd="rel"), ValueError),
        (lambda f: f.mkdir("/x", user="root"), ValueError),
        (lambda f: f.mkdir("/x", recursive="yes"), TypeError),
        (lambda f: f.write_file("/x", 3), TypeError),
    ],
)
async def test_invalid_arguments_raise_before_exec(call, error) -> None:
    container = Container()

    with pytest.raises(error):
        await call(Files(container))
    assert container.calls == []


async def test_read_file_streams_bytes_then_checks_the_final_frame() -> None:
    container = Container()
    container.script(stdout=[b"hello ", b"world"], stderr=[success(), success()])

    response = await Files(container).read_file("/f")

    assert container.calls[0][1] == {"stdout": "pipe", "stderr": "pipe"}
    assert await response.body.read_all() == b"hello world"


async def test_read_file_reads_ahead_until_the_opening_frame_is_complete() -> None:
    opening = success()
    stderr_rest = asyncio.get_running_loop().create_future()
    container = Container()
    process = container.script(
        stdout=[b"early ", b"bytes"], stderr=[opening[:4], stderr_rest]
    )

    reading = asyncio.ensure_future(Files(container).read_file("/f"))
    for _ in range(20):
        await asyncio.sleep(0)
    # The transport releases the rest of the frame only once stdout was read.
    assert process.stdout.reader.chunks == []
    stderr_rest.set_result(opening[4:] + success())

    response = await reading
    assert await response.body.read_all() == b"early bytes"


async def test_read_file_open_error() -> None:
    container = Container()
    container.script(stderr=[errno_frame(21, "is a directory")])

    with pytest.raises(SandboxFileError, match="read_file '/d': is a directory"):
        await Files(container).read_file("/d")


async def test_read_file_late_error_errors_the_body() -> None:
    container = Container()
    container.script(stdout=[b"part"], stderr=[success(), errno_frame(5, "io")])

    response = await Files(container).read_file("/f")

    with pytest.raises(Exception, match="read_file '/f': io") as caught:
        await response.body.read_all()
    assert type(caught.value).__name__ == "JsError"
    assert getattr(caught.value, "name") == "SandboxFileError"  # noqa: B009


async def test_write_file_streams_content_and_closes_stdin() -> None:
    container = Container()
    process = container.script(stdout=[success(), success()])

    async def chunks():
        yield b"a"
        yield bytearray(b"b")

    await Files(container).write_file("/f", chunks())

    assert container.calls[0][1] == {
        "stdin": "pipe",
        "stdout": "pipe",
        "stderr": "ignore",
    }
    assert process.stdin.written == b"ab"
    assert process.stdin.closed


async def test_write_file_accepts_str_and_js_streams() -> None:
    container = Container()
    first = container.script(stdout=[success(), success()])
    second = container.script(stdout=[success(), success()])
    files = Files(container)

    await files.write_file("/a", "héllo")
    await files.write_file("/b", Stream([b"x", b"y"]))

    assert first.stdin.written == "héllo".encode()
    assert second.stdin.written == b"xy"


async def test_write_file_open_error_cancels_the_source() -> None:
    container = Container()
    container.script(stdout=[errno_frame(13, "denied")])
    source = Stream([b"x"])

    with pytest.raises(SandboxFileError, match="write_file '/f': denied"):
        await Files(container).write_file("/f", source)
    await asyncio.sleep(0)
    assert source.cancelled


async def test_write_file_bad_chunk_is_a_type_error() -> None:
    container = Container()
    container.script(stdout=[success(), success()])

    async def chunks():
        yield "text"

    with pytest.raises(TypeError, match="bytes-like"):
        await Files(container).write_file("/f", chunks())


async def test_abort_kills_the_process_and_raises() -> None:
    abort = asyncio.Event()
    never = asyncio.get_running_loop().create_future()
    container = Container()
    container.script(stdout=[never])

    task = asyncio.ensure_future(Files(container).mkdir("/x", abort=abort))
    await asyncio.sleep(0.01)
    abort.set()

    with pytest.raises(SandboxAbortError):
        await task
    assert container.calls[0][1]["signal"].aborted


async def test_already_set_abort_never_execs() -> None:
    abort = asyncio.Event()
    abort.set()
    container = Container()

    with pytest.raises(SandboxAbortError):
        await Files(container).stat("/x", abort=abort)
    assert container.calls == []
