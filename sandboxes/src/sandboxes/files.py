import asyncio
import re
import struct
from collections.abc import AsyncIterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from . import _streams
from ._shim import SHIM_PATH, Container, ShimControl, ShimSession
from .errors import FileOperation, SandboxFileError, SandboxProtocolError

__all__ = (
    "FileContent",
    "Files",
    "SandboxDirectoryEntry",
    "SandboxFileStat",
    "SandboxFileType",
)

type SandboxFileType = Literal[
    "file",
    "directory",
    "symlink",
    "block_device",
    "character_device",
    "fifo",
    "socket",
]
type FileContent = str | bytes | bytearray | memoryview | AsyncIterable[bytes] | Any
"""``str``, bytes-like, an async iterable of bytes, or a JS ``ReadableStream``."""

_FILE_TYPES: tuple[SandboxFileType, ...] = (
    "file",
    "directory",
    "symlink",
    "block_device",
    "character_device",
    "fifo",
    "socket",
)
_STAT = struct.Struct("<BQIIIqqq")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_USER = re.compile(r"[0-9]+:[0-9]+")


@dataclass(frozen=True, slots=True)
class SandboxFileStat:
    """
    Metadata for a path in the container.

    Attributes
    ----------
    type
        The kind of path.
    size
        The size in bytes.
    mode
        The permission bits.
    uid
        The owner's user ID.
    gid
        The owner's group ID.
    accessed_at
        The last access time, in UTC.
    modified_at
        The last time the contents changed, in UTC.
    changed_at
        The last time the metadata changed, in UTC.
    """

    type: SandboxFileType
    size: int
    mode: int
    uid: int
    gid: int
    accessed_at: datetime
    modified_at: datetime
    changed_at: datetime

    @classmethod
    def _from_payload(cls, payload: bytes, operation: str) -> "SandboxFileStat":
        if len(payload) != _STAT.size:
            raise SandboxProtocolError(
                f"sandbox-shim returned invalid {operation} data"
            )

        type_byte, size, mode, uid, gid, accessed, modified, changed = _STAT.unpack(
            payload
        )
        return cls(
            type=_decode_file_type(type_byte),
            size=size,
            mode=mode,
            uid=uid,
            gid=gid,
            accessed_at=_decode_time(accessed),
            modified_at=_decode_time(modified),
            changed_at=_decode_time(changed),
        )


@dataclass(frozen=True, slots=True)
class SandboxDirectoryEntry:
    """
    One entry of a directory.

    Attributes
    ----------
    name
        The entry's name, without its directory.
    type
        The kind of entry. A symbolic link is ``"symlink"``, not its target.
    """

    name: str
    type: SandboxFileType


def _decode_file_type(value: int) -> SandboxFileType:
    if value >= len(_FILE_TYPES):
        raise SandboxProtocolError("sandbox-shim returned an unknown file type")
    return _FILE_TYPES[value]


def _decode_time(milliseconds: int) -> datetime:
    try:
        return _EPOCH + timedelta(milliseconds=milliseconds)
    except OverflowError:
        raise SandboxProtocolError(
            "sandbox-shim returned an out-of-range timestamp"
        ) from None


def _decode_entries(payload: bytes) -> list[SandboxDirectoryEntry]:
    reader = _PayloadReader(payload)
    entries = []
    for _ in range(reader.unpack("<I")):
        file_type = _decode_file_type(reader.unpack("<B"))
        name_bytes = reader.take(reader.unpack("<H"))
        try:
            # A leading byte-order mark stays in the name.
            name = name_bytes.decode()
        except UnicodeDecodeError:
            raise SandboxProtocolError(
                "sandbox-shim returned invalid UTF-8 in directory entry name"
            ) from None
        entries.append(SandboxDirectoryEntry(name, file_type))

    if reader.offset != len(payload):
        raise SandboxProtocolError("sandbox-shim returned trailing directory data")
    return entries


class _PayloadReader:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0

    def take(self, length: int) -> bytes:
        if self.offset + length > len(self.payload):
            raise SandboxProtocolError("sandbox-shim returned truncated directory data")
        chunk = self.payload[self.offset : self.offset + length]
        self.offset += length
        return chunk

    def unpack(self, fmt: str) -> int:
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]


@dataclass(frozen=True, slots=True)
class _FileOptions:
    """The options every ``Files`` method takes."""

    cwd: str | None = None
    user: str | None = None
    abort: asyncio.Event | None = None

    def __post_init__(self) -> None:
        if self.cwd is not None:
            if not isinstance(self.cwd, str):
                raise TypeError("cwd must be a string")
            if not self.cwd.startswith("/"):
                raise ValueError("cwd must be an absolute path")
        if self.user is not None:
            if not isinstance(self.user, str):
                raise TypeError("user must be a string")
            if not _USER.fullmatch(self.user):
                raise ValueError(
                    'user must be numeric user and group IDs, as "uid:gid"'
                )
        if self.abort is not None and not isinstance(self.abort, asyncio.Event):
            raise TypeError("abort must be an asyncio.Event")

    def resolve(self, path: str) -> str:
        """
        Validate ``path`` and join a relative one onto ``cwd``.

        The shim receives only absolute paths and ``exec()`` never receives
        ``cwd``, so a missing ``cwd`` fails as ``ENOENT``.
        """
        if not isinstance(path, str):
            raise TypeError("path must be a string")
        if not path:
            raise ValueError("path must not be empty")
        if "\0" in path:
            raise ValueError("path cannot contain NUL characters")
        if path.startswith("/"):
            return path
        if self.cwd is None:
            raise ValueError("cwd is required when path is relative")
        return f"{self.cwd}/{path}"

    async def start(
        self, container: Container, command: str, paths: Sequence[str], **stdio: str
    ) -> ShimSession:
        exec_options: dict[str, Any] = dict(stdio)
        if self.user is not None:
            exec_options["user"] = self.user
        return await ShimSession.start(
            container, [SHIM_PATH, command, *paths], exec_options, self.abort
        )


def _check_flag(name: str, value: object) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")


class Files:
    """
    File operations in a running container.

    Every method is a coroutine and accepts ``cwd``, ``user`` and ``abort``.

    The image must provide ``/usr/local/bin/sandbox-shim`` from the
    ``cloudflare/sandbox`` image this package was tested against.

    Parameters
    ----------
    container
        ``ctx.container``, or any object with a compatible ``exec``.
    """

    def __init__(self, container: Container) -> None:
        self._container = container

    def __repr__(self) -> str:
        return f"<Files container={self._container!r}>"

    async def read_file(
        self,
        path: str,
        /,
        *,
        cwd: str | None = None,
        user: str | None = None,
        abort: asyncio.Event | None = None,
    ) -> Any:
        """
        Stream a file's bytes.

        Parameters
        ----------
        path
            The file to read.
        cwd
            An absolute directory that relative paths are joined onto. Without
            it, relative paths raise ``ValueError``.
        user
            The numeric user and group the shim runs as, as ``"uid:gid"``.
            Defaults to the container's user.
        abort
            Set it to stop the operation. The call then raises
            ``SandboxAbortError``.

        Returns
        -------
        js.Response
            A response whose body applies backpressure to the shim. A failure
            after the file opened errors the body.

        Raises
        ------
        SandboxFileError
            The file could not be opened.
        SandboxProtocolError
            The shim's reply was invalid.
        """
        options = _FileOptions(cwd, user, abort)
        session = await options.start(
            self._container,
            "read",
            [options.resolve(path)],
            stdout="pipe",
            stderr="pipe",
        )
        return await _streams.read_file(session, path)

    async def write_file(
        self,
        path: str,
        content: FileContent,
        /,
        *,
        cwd: str | None = None,
        user: str | None = None,
        abort: asyncio.Event | None = None,
    ) -> None:
        """
        Create or truncate a file and stream ``content`` into it.

        The file is opened before ``content`` is read, so a later failure can
        leave it created, truncated or partly written.

        Parameters
        ----------
        path
            The file to write. Its parent directory must exist.
        content
            ``str`` (written as UTF-8), bytes-like, an async iterable of
            bytes, or a JS ``ReadableStream`` of ``Uint8Array`` chunks.
        cwd
            An absolute directory that relative paths are joined onto. Without
            it, relative paths raise ``ValueError``.
        user
            The numeric user and group the shim runs as, as ``"uid:gid"``.
            Defaults to the container's user.
        abort
            Set it to stop the operation. The call then raises
            ``SandboxAbortError``.

        Raises
        ------
        TypeError
            ``content`` has an unsupported type.
        SandboxFileError
            Linux rejected the write, for example with ``ENOENT`` or
            ``EISDIR``.
        """
        options = _FileOptions(cwd, user, abort)
        resolved = options.resolve(path)
        source = _streams.Source(content)
        try:
            session = await options.start(
                self._container,
                "write",
                [resolved],
                stdin="pipe",
                stdout="pipe",
                stderr="ignore",
            )
        except BaseException:
            source.discard()
            raise
        await _streams.write_file(session, source, path)

    async def stat(
        self,
        path: str,
        /,
        *,
        cwd: str | None = None,
        user: str | None = None,
        abort: asyncio.Event | None = None,
    ) -> SandboxFileStat:
        """
        Return metadata for ``path``, following a final symbolic link.

        Parameters
        ----------
        path
            The path to describe.
        cwd
            An absolute directory that relative paths are joined onto. Without
            it, relative paths raise ``ValueError``.
        user
            The numeric user and group the shim runs as, as ``"uid:gid"``.
            Defaults to the container's user.
        abort
            Set it to stop the operation. The call then raises
            ``SandboxAbortError``.

        Returns
        -------
        SandboxFileStat

        Raises
        ------
        SandboxFileError
            Linux rejected the call, for example with ``ENOENT``.
        """
        payload = await self._run(
            "stat", path, _FileOptions(cwd, user, abort), expected="data"
        )
        return SandboxFileStat._from_payload(payload, "stat")

    async def lstat(
        self,
        path: str,
        /,
        *,
        cwd: str | None = None,
        user: str | None = None,
        abort: asyncio.Event | None = None,
    ) -> SandboxFileStat:
        """
        Return metadata for ``path`` without following a final symbolic link.

        Parameters
        ----------
        path
            The path to describe.
        cwd
            An absolute directory that relative paths are joined onto. Without
            it, relative paths raise ``ValueError``.
        user
            The numeric user and group the shim runs as, as ``"uid:gid"``.
            Defaults to the container's user.
        abort
            Set it to stop the operation. The call then raises
            ``SandboxAbortError``.

        Returns
        -------
        SandboxFileStat

        Raises
        ------
        SandboxFileError
            Linux rejected the call, for example with ``ENOENT``.
        """
        payload = await self._run(
            "lstat", path, _FileOptions(cwd, user, abort), expected="data"
        )
        return SandboxFileStat._from_payload(payload, "lstat")

    async def read_directory(
        self,
        path: str,
        /,
        *,
        cwd: str | None = None,
        user: str | None = None,
        abort: asyncio.Event | None = None,
    ) -> list[SandboxDirectoryEntry]:
        """
        List the immediate entries of a directory, in native order.

        Parameters
        ----------
        path
            The directory to list.
        cwd
            An absolute directory that relative paths are joined onto. Without
            it, relative paths raise ``ValueError``.
        user
            The numeric user and group the shim runs as, as ``"uid:gid"``.
            Defaults to the container's user.
        abort
            Set it to stop the operation. The call then raises
            ``SandboxAbortError``.

        Returns
        -------
        list[SandboxDirectoryEntry]

        Raises
        ------
        SandboxFileError
            Linux rejected the call, for example with ``ENOTDIR``.
        """
        payload = await self._run(
            "read_directory", path, _FileOptions(cwd, user, abort), expected="data"
        )
        return _decode_entries(payload)

    async def mkdir(
        self,
        path: str,
        /,
        *,
        recursive: bool = False,
        cwd: str | None = None,
        user: str | None = None,
        abort: asyncio.Event | None = None,
    ) -> None:
        """
        Create a directory, and with ``recursive``, its missing parents.

        Parameters
        ----------
        path
            The directory to create.
        recursive
            Create missing parents too.
        cwd
            An absolute directory that relative paths are joined onto. Without
            it, relative paths raise ``ValueError``.
        user
            The numeric user and group the shim runs as, as ``"uid:gid"``.
            Defaults to the container's user.
        abort
            Set it to stop the operation. The call then raises
            ``SandboxAbortError``.

        Raises
        ------
        SandboxFileError
            Linux rejected the call, for example with ``EEXIST``.
        """
        _check_flag("recursive", recursive)
        await self._run(
            "mkdir",
            path,
            _FileOptions(cwd, user, abort),
            flags=["--recursive"] if recursive else [],
        )

    async def rename(
        self,
        source: str,
        destination: str,
        /,
        *,
        cwd: str | None = None,
        user: str | None = None,
        abort: asyncio.Event | None = None,
    ) -> None:
        """
        Rename a path with Linux ``rename`` rules.

        Crossing filesystems fails with ``EXDEV``. Nothing is copied instead.

        Parameters
        ----------
        source
            The path to rename.
        destination
            The new path.
        cwd
            An absolute directory that relative paths are joined onto. Without
            it, relative paths raise ``ValueError``.
        user
            The numeric user and group the shim runs as, as ``"uid:gid"``.
            Defaults to the container's user.
        abort
            Set it to stop the operation. The call then raises
            ``SandboxAbortError``.

        Raises
        ------
        SandboxFileError
            Linux rejected the call, for example with ``ENOENT`` or ``EXDEV``.
        """
        await self._run(
            "rename",
            source,
            _FileOptions(cwd, user, abort),
            destination=destination,
        )

    async def remove(
        self,
        path: str,
        /,
        *,
        recursive: bool = False,
        force: bool = False,
        cwd: str | None = None,
        user: str | None = None,
        abort: asyncio.Event | None = None,
    ) -> None:
        """
        Remove a file, a symbolic link, or with ``recursive``, a directory tree.

        Parameters
        ----------
        path
            The path to remove.
        recursive
            Remove a directory and everything in it.
        force
            Succeed when ``path`` doesn't exist. Other errors still raise.
        cwd
            An absolute directory that relative paths are joined onto. Without
            it, relative paths raise ``ValueError``.
        user
            The numeric user and group the shim runs as, as ``"uid:gid"``.
            Defaults to the container's user.
        abort
            Set it to stop the operation. The call then raises
            ``SandboxAbortError``.

        Raises
        ------
        SandboxFileError
            Linux rejected the call, for example with ``ENOENT`` or
            ``EISDIR``.
        """
        _check_flag("recursive", recursive)
        _check_flag("force", force)
        flags = [
            flag
            for flag, enabled in (("--recursive", recursive), ("--force", force))
            if enabled
        ]
        await self._run("remove", path, _FileOptions(cwd, user, abort), flags=flags)

    async def _run(
        self,
        operation: FileOperation,
        path: str,
        options: _FileOptions,
        *,
        destination: str | None = None,
        flags: Sequence[str] = (),
        expected: Literal["success", "data"] = "success",
    ) -> bytes:
        """Run a command that answers with one frame, and return its payload."""
        paths = [options.resolve(path)]
        if destination is not None:
            paths.append(options.resolve(destination))
        session = await options.start(
            self._container,
            operation.replace("_", "-"),
            [*paths, *flags],
            stdout="pipe",
            stderr="ignore",
        )

        control: ShimControl | None = None
        try:
            control = session.stdout_control()
            frame = await control.read_frame()
            await control.expect_end()
            if frame.kind == "file_error":
                await session.settle()
                raise SandboxFileError.from_errno(
                    frame.errno,
                    frame.detail,
                    operation,
                    path,
                    destination=destination,
                )
            if frame.kind != expected:
                raise SandboxProtocolError(
                    "sandbox-shim did not return command data"
                    if expected == "data"
                    else "sandbox-shim did not confirm command completion"
                )

            await session.expect_exit()
            control.release_lock()
            session.finish()
            return frame.payload
        except BaseException:
            session.terminate()
            if control is not None:
                control.discard()
            raise
