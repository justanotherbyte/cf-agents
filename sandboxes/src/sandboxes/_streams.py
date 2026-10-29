"""The two ``Files`` commands that stream file bytes: ``read`` and ``write``."""

import asyncio
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from . import _ffi
from ._shim import (
    Frame,
    ShimControl,
    ShimSession,
    abort_writer,
    cancel_reader,
    spawn,
)
from .errors import SandboxFileError, SandboxProtocolError

__all__ = ("Source", "read_file", "write_file")


async def read_file(session: ShimSession, path: str) -> Any:
    """Return a JS ``Response`` whose body pulls file bytes from the shim."""
    control: ShimControl | None = None
    stdout: _StdoutReader | None = None
    try:
        control = session.stderr_control()
        stdout = _StdoutReader(session.stdout_reader())
        opening = await control.read_frame()
        stdout.stop_reading_ahead()
        if opening.kind == "file_error":
            await control.expect_end()
            await session.settle()
            raise SandboxFileError.from_errno(
                opening.errno, opening.detail, "read_file", path
            )
        if opening.kind != "success":
            raise SandboxProtocolError("sandbox-shim returned data before file bytes")

        terminal = asyncio.ensure_future(_read_terminal(control))
        terminal.add_done_callback(_retrieve)
        body = _ResponseBody(session, control, stdout, terminal, path)
        return _ffi.response(_ffi.readable_stream(body.pull, body.cancel))
    except BaseException:
        _terminate_read(session, control, stdout)
        raise


def _retrieve(future: asyncio.Future[Any]) -> None:
    if not future.cancelled():
        future.exception()


async def _read_terminal(control: ShimControl) -> Frame:
    frame = await control.read_frame()
    await control.expect_end()
    return frame


def _terminate_read(
    session: ShimSession, control: ShimControl | None, stdout: "_StdoutReader | None"
) -> None:
    session.terminate()
    if control is not None:
        control.discard()
    if stdout is not None:
        stdout.discard()


class _StdoutReader:
    """
    Reads the shim's stdout, reading ahead until the opening frame arrives.

    A container transport can carry stdout and stderr over one connection with
    shared backpressure, and deliver file bytes before the opening frame is
    complete. The rest of the frame then arrives only after something reads
    stdout. Once the frame is complete, reads happen only as the body is read,
    so the caller's pace applies backpressure again.
    """

    def __init__(self, reader: Any) -> None:
        self._reader = reader
        self._chunks: deque[bytes] = deque()
        self._pending: asyncio.Future[Any] | None = None
        self._reading_ahead = True
        self._read_next()

    def stop_reading_ahead(self) -> None:
        """Chunks already read stay queued for ``read()``."""
        self._reading_ahead = False

    async def read(self) -> bytes | None:
        """The next chunk, or ``None`` at end of file."""
        if self._chunks:
            return self._chunks.popleft()

        pending = self._pending or asyncio.ensure_future(self._reader.read())
        self._pending = None
        result = await pending
        return None if result.done else _ffi.to_bytes(result.value)

    def discard(self) -> None:
        spawn(cancel_reader(self._reader))

    def release_lock(self) -> None:
        self._reader.releaseLock()

    def _read_next(self) -> None:
        read = asyncio.ensure_future(self._reader.read())
        self._pending = read
        read.add_done_callback(self._on_read)

    def _on_read(self, read: asyncio.Future[Any]) -> None:
        if read.cancelled() or read.exception() is not None:
            return
        result = read.result()
        if self._pending is not read or result.done:
            return

        self._pending = None
        self._chunks.append(_ffi.to_bytes(result.value))
        if self._reading_ahead:
            self._read_next()


class _ResponseBody:
    """The pull and cancel callbacks of a ``read_file`` response body."""

    def __init__(
        self,
        session: ShimSession,
        control: ShimControl,
        stdout: _StdoutReader,
        terminal: asyncio.Future[Frame],
        path: str,
    ) -> None:
        self._session = session
        self._control = control
        self._stdout = stdout
        self._terminal = terminal
        self._path = path

    async def pull(self, controller: Any) -> bool:
        try:
            chunk = await self._session.wait_for(self._stdout.read())
            if chunk is not None:
                controller.enqueue(_ffi.to_uint8array(chunk))
                return False

            await self._finish()
            controller.close()
        except Exception as error:
            _terminate_read(self._session, self._control, self._stdout)
            controller.error(_ffi.to_js_error(error))
        return True

    async def _finish(self) -> None:
        terminal = await self._session.wait_for(self._terminal)
        if terminal.kind == "file_error":
            await self._session.settle()
            raise SandboxFileError.from_errno(
                terminal.errno, terminal.detail, "read_file", self._path
            )
        if terminal.kind != "success":
            raise SandboxProtocolError("sandbox-shim returned data after file bytes")

        self._stdout.release_lock()
        self._control.release_lock()
        self._session.finish()

    def cancel(self, reason: Any) -> None:
        _terminate_read(self._session, self._control, self._stdout)


class Source:
    """
    The content of ``write_file``, read as ``bytes`` chunks.

    Raises
    ------
    TypeError
        ``content`` isn't ``str``, bytes-like, an async iterable of bytes, or a
        JS ``ReadableStream``.
    """

    def __init__(self, content: Any) -> None:
        self._stream: Any = None
        self._reader: Any = None
        self._iterator: AsyncIterator[Any] | None = None
        self._chunks: deque[bytes] = deque()

        if isinstance(content, str):
            self._chunks.append(content.encode())
        elif isinstance(content, bytes | bytearray | memoryview):
            self._chunks.append(bytes(content))
        elif _ffi.is_readable_stream(content):
            self._stream = content
        elif isinstance(content, AsyncIterable):
            self._iterator = aiter(content)
        else:
            raise TypeError(
                "content must be str, bytes-like, an async iterable of bytes, "
                "or a ReadableStream"
            )

    async def read(self) -> bytes | None:
        """The next chunk, or ``None`` when the content ends."""
        if self._chunks:
            return self._chunks.popleft()
        if self._iterator is not None:
            return await self._read_iterator(self._iterator)
        if self._stream is None:
            return None

        if self._reader is None:
            self._reader = self._stream.getReader()
        result = await self._reader.read()
        if result.done:
            return None
        if not _ffi.is_bytes_chunk(result.value):
            raise TypeError("write_file stream chunks must be Uint8Array values")
        return _ffi.to_bytes(result.value)

    @staticmethod
    async def _read_iterator(iterator: AsyncIterator[Any]) -> bytes | None:
        try:
            chunk = await anext(iterator)
        except StopAsyncIteration:
            return None
        if not isinstance(chunk, bytes | bytearray | memoryview):
            raise TypeError("write_file content chunks must be bytes-like")
        return bytes(chunk)

    def release(self) -> None:
        if self._reader is not None:
            self._reader.releaseLock()

    def discard(self) -> None:
        if self._reader is not None:
            spawn(cancel_reader(self._reader))
        elif self._stream is not None:
            spawn(self._stream.cancel())
        elif self._iterator is not None and hasattr(self._iterator, "aclose"):
            spawn(self._iterator.aclose())  # ty: ignore[call-non-callable] checked by hasattr


async def write_file(session: ShimSession, source: Source, path: str) -> None:
    control: ShimControl | None = None
    stdin: Any = None
    tasks: list[asyncio.Future[Any]] = []
    try:
        control = session.stdout_control()
        stdin = session.stdin_writer()
        opening = await control.read_frame()
        if opening.kind == "file_error":
            await control.expect_end()
            await session.settle()
        _expect_success(opening, path)

        pumping = asyncio.ensure_future(_pump(session, source, stdin))
        terminal = asyncio.ensure_future(_read_write_terminal(session, control))
        tasks += (pumping, terminal)
        await _settle_write(pumping, terminal, path)

        await session.expect_exit()
        source.release()
        stdin.releaseLock()
        control.release_lock()
        session.finish()
    except BaseException:
        for task in tasks:
            task.cancel()
        session.terminate()
        if stdin is not None:
            spawn(abort_writer(stdin))
        if control is not None:
            control.discard()
        source.discard()
        raise


async def _settle_write(
    pumping: "asyncio.Future[_PumpFailure | None]",
    terminal: "asyncio.Future[Frame | BaseException]",
    path: str,
) -> None:
    """Decide the outcome from whichever of the pump and the shim finishes first."""
    done, _ = await asyncio.wait(
        {pumping, terminal}, return_when=asyncio.FIRST_COMPLETED
    )
    if pumping not in done:
        _expect_terminal(terminal.result(), path)
        _raise_pump_failure(await pumping)
        return

    failure = pumping.result()
    if failure is None:
        _expect_terminal(await terminal, path)
        return
    if failure.source:
        raise failure.error

    # The shim may have closed stdin because it failed; report that failure.
    result = await terminal
    if isinstance(result, Frame) and result.kind == "file_error":
        _expect_success(result, path)
    if isinstance(result, BaseException):
        raise result
    raise failure.error


class _PumpFailure:
    __slots__ = ("error", "source")

    def __init__(self, error: BaseException, *, source: bool) -> None:
        self.error = error
        self.source = source


async def _pump(
    session: ShimSession, source: Source, stdin: Any
) -> _PumpFailure | None:
    """Copy ``source`` into stdin, and report which side failed, if any."""
    while True:
        try:
            await session.wait_for(stdin.ready)
        except Exception as error:
            return _PumpFailure(error, source=False)

        try:
            chunk = await session.wait_for(source.read())
        except Exception as error:
            return _PumpFailure(error, source=True)

        try:
            if chunk is None:
                await session.wait_for(stdin.close())
                return None
            await session.wait_for(stdin.write(_ffi.to_uint8array(chunk)))
        except Exception as error:
            return _PumpFailure(error, source=False)


async def _read_write_terminal(
    session: ShimSession, control: ShimControl
) -> Frame | BaseException:
    try:
        frame = await control.read_frame()
        await control.expect_end()
        if frame.kind == "file_error":
            await session.settle()
        return frame
    except Exception as error:
        return error


def _expect_terminal(result: Frame | BaseException, path: str) -> None:
    if isinstance(result, BaseException):
        raise result
    _expect_success(result, path)


def _raise_pump_failure(failure: _PumpFailure | None) -> None:
    if failure is not None:
        raise failure.error


def _expect_success(frame: Frame, path: str) -> None:
    if frame.kind == "file_error":
        raise SandboxFileError.from_errno(frame.errno, frame.detail, "write_file", path)
    if frame.kind != "success":
        raise SandboxProtocolError("sandbox-shim returned data while writing a file")
