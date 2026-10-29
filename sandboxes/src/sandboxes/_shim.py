"""
The control protocol of ``/usr/local/bin/sandbox-shim``, version 1.

A frame is a 10-byte header (``SBXF``, version, kind, little-endian ``u32``
payload length) followed by the payload.
"""

import asyncio
import json
import struct
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self

from . import _ffi
from .errors import SandboxAbortError, SandboxProtocolError

__all__ = (
    "SHIM_PATH",
    "abort_writer",
    "cancel_reader",
    "Container",
    "Frame",
    "InterceptContainer",
    "ShimControl",
    "ShimSession",
    "parse_json_payload",
    "spawn",
)

SHIM_PATH = "/usr/local/bin/sandbox-shim"

_MAGIC = b"SBXF"
_PROTOCOL_VERSION = 1
_HEADER = struct.Struct("<4sBBI")
_ERRNO = struct.Struct("<i")
_MAX_ERROR_PAYLOAD = 65540
_FRAME_KINDS = {0: "success", 1: "file_error", 2: "data"}

_background: set[asyncio.Future[Any]] = set()


class Container(Protocol):
    """``ctx.container``, or any object with a compatible ``exec``."""

    def exec(self, cmd: Any, options: Any, /) -> Awaitable[Any]: ...


class InterceptContainer(Container, Protocol):
    def interceptOutboundHttp(self, host: str, fetcher: Any, /) -> Awaitable[Any]: ...


@dataclass(frozen=True, slots=True)
class Frame:
    kind: Literal["success", "file_error", "data"]
    payload: bytes = b""
    errno: int = 0
    detail: str = ""


def spawn(awaitable: Awaitable[Any]) -> None:
    """Run cleanup in the background and ignore how it ends."""
    future = asyncio.ensure_future(awaitable)
    _background.add(future)
    future.add_done_callback(_forget)


def _forget(future: asyncio.Future[Any]) -> None:
    _background.discard(future)
    if not future.cancelled():
        future.exception()


def parse_json_payload(payload: bytes, invalid: str) -> Any:
    """Decode a data frame's payload as JSON, or raise ``invalid``."""
    try:
        return json.loads(payload.decode(), parse_constant=_reject_constant)
    except ValueError:
        raise SandboxProtocolError(invalid) from None


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


class ShimSession:
    """
    One ``sandbox-shim`` process and the caller's ``abort`` event.

    With ``kill_on_abort``, setting ``abort`` kills the process through the
    signal passed to ``exec()``. Without it, the caller closes stdin instead
    and the shim cleans up.
    """

    def __init__(self, abort: asyncio.Event | None, *, kill_on_abort: bool) -> None:
        self.process: Any = None
        self._settled = False
        self._controller = None
        self._aborted: asyncio.Future[Any] | None = None
        if abort is None:
            return

        if kill_on_abort:
            self._controller = _ffi.abort_controller()
        self._aborted = asyncio.ensure_future(abort.wait())
        self._aborted.add_done_callback(self._on_abort)

    @classmethod
    async def start(
        cls,
        container: Container,
        command: list[str],
        options: dict[str, Any],
        abort: asyncio.Event | None,
        *,
        kill_on_abort: bool = True,
    ) -> Self:
        if abort is not None and abort.is_set():
            raise SandboxAbortError()

        session = cls(abort, kill_on_abort=kill_on_abort)
        if session._controller is not None:
            options = {**options, "signal": session._controller.signal}
        starting = asyncio.ensure_future(
            container.exec(_ffi.to_js_object(command), _ffi.to_js_object(options))
        )
        try:
            session.process = await session.wait_for(starting)
        except BaseException:
            session.finish()
            if not kill_on_abort:
                starting.add_done_callback(_close_started_stdin)
            raise
        return session

    def _on_abort(self, waiter: asyncio.Future[Any]) -> None:
        if not waiter.cancelled() and self._controller is not None:
            self._controller.abort()

    async def wait_for[T](self, awaitable: Awaitable[T]) -> T:
        """
        Await ``awaitable``, or raise ``SandboxAbortError`` once ``abort`` is set.

        An abort leaves ``awaitable`` running, as ``Promise.race`` does.
        """
        if self._aborted is None:
            return await awaitable

        task = asyncio.ensure_future(awaitable)
        try:
            await asyncio.wait(
                {task, self._aborted}, return_when=asyncio.FIRST_COMPLETED
            )
        except BaseException:
            task.cancel()
            raise

        if task.done():
            return task.result()
        task.add_done_callback(_forget)
        raise SandboxAbortError()

    def stop_following_abort(self) -> None:
        """Later waits no longer raise when ``abort`` is set."""
        if self._aborted is not None:
            self._aborted.cancel()
            self._aborted = None

    async def exit_code(self) -> int:
        return await self.wait_for(self.process.exitCode)

    async def expect_exit(self) -> None:
        exit_code = await self.exit_code()
        if exit_code != 0:
            raise SandboxProtocolError(f"sandbox-shim exited with code {exit_code}")

    def finish(self) -> None:
        if self._settled:
            return
        self._settled = True
        self.stop_following_abort()

    async def settle(self) -> None:
        """
        Wait for a shim that already reported its outcome to exit.

        Never raises: the reported outcome stays authoritative, and if the exit
        isn't observed, ``terminate()`` still cleans up.
        """
        try:
            await self.exit_code()
            self.finish()
        except Exception:
            pass

    def terminate(self) -> None:
        if self._settled:
            return
        self._settled = True
        self.stop_following_abort()
        try:
            self.process.kill(9)
        except Exception:
            pass

    def stdout_control(self) -> "ShimControl":
        return ShimControl(self._stream("stdout").getReader(), self)

    def stderr_control(self) -> "ShimControl":
        return ShimControl(self._stream("stderr").getReader(), self)

    def stdout_reader(self) -> Any:
        return self._stream("stdout").getReader()

    def stdin_writer(self) -> Any:
        return self._stream("stdin").getWriter()

    def _stream(self, name: str) -> Any:
        stream = getattr(self.process, name, None)
        if _ffi.is_absent(stream):
            raise SandboxProtocolError(f"sandbox-shim did not provide {name}")
        return stream


def _close_started_stdin(starting: asyncio.Future[Any]) -> None:
    if starting.cancelled() or starting.exception() is not None:
        return
    stdin = getattr(starting.result(), "stdin", None)
    if stdin is not None and not _ffi.is_absent(stdin):
        spawn(stdin.close())


class ShimControl:
    """Reads frames from one of the shim's output streams."""

    def __init__(self, reader: Any, session: ShimSession) -> None:
        self._reader = reader
        self._session = session
        self._pending = b""

    async def read_frame(self) -> Frame:
        magic, version, kind_byte, length = _HEADER.unpack(
            await self._read_exactly(_HEADER.size)
        )
        if magic != _MAGIC:
            raise SandboxProtocolError("sandbox-shim returned invalid protocol magic")
        if version != _PROTOCOL_VERSION:
            raise SandboxProtocolError(
                f"sandbox-shim protocol {version} is not supported"
            )

        kind = _FRAME_KINDS.get(kind_byte)
        if kind is None:
            raise SandboxProtocolError(
                f"sandbox-shim returned unknown control status {kind_byte}"
            )
        if kind == "success":
            if length != 0:
                raise SandboxProtocolError("sandbox-shim returned invalid control data")
            return Frame("success")
        if kind == "data":
            return Frame("data", await self._read_exactly(length))
        return await self._read_error(length)

    async def _read_error(self, length: int) -> Frame:
        if length < _ERRNO.size:
            raise SandboxProtocolError("sandbox-shim returned invalid control data")
        if length > _MAX_ERROR_PAYLOAD:
            raise SandboxProtocolError(
                "sandbox-shim error message exceeded its size limit"
            )

        payload = await self._read_exactly(length)
        (errno,) = _ERRNO.unpack_from(payload)
        if errno <= 0:
            raise SandboxProtocolError(f"sandbox-shim returned invalid errno {errno}")
        try:
            detail = payload[_ERRNO.size :].decode()
        except UnicodeDecodeError:
            raise SandboxProtocolError(
                "sandbox-shim returned a non-UTF-8 error message"
            ) from None
        return Frame("file_error", errno=errno, detail=detail)

    async def expect_end(self) -> None:
        if (
            self._pending
            or not (await self._session.wait_for(self._reader.read())).done
        ):
            raise SandboxProtocolError("sandbox-shim returned trailing control data")

    def discard(self) -> None:
        spawn(cancel_reader(self._reader))

    def release_lock(self) -> None:
        self._reader.releaseLock()

    async def _read_exactly(self, length: int) -> bytes:
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            if not self._pending:
                result = await self._session.wait_for(self._reader.read())
                if result.done:
                    raise SandboxProtocolError(
                        "sandbox-shim returned truncated control data"
                    )
                self._pending = _ffi.to_bytes(result.value)

            chunks.append(self._pending[:remaining])
            remaining -= len(chunks[-1])
            self._pending = self._pending[len(chunks[-1]) :]
        return b"".join(chunks)


async def abort_writer(writer: Any) -> None:
    try:
        await writer.abort()
    finally:
        writer.releaseLock()


async def cancel_reader(reader: Any) -> None:
    try:
        await reader.cancel()
    finally:
        reader.releaseLock()
