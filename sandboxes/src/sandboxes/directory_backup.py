import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict, cast
from urllib.parse import urlsplit

from workers import WorkerEntrypoint

from . import _ffi
from ._shim import (
    SHIM_PATH,
    InterceptContainer,
    ShimControl,
    ShimSession,
    parse_json_payload,
    spawn,
)
from ._utils import is_absolute_path
from .errors import (
    BackupErrorCode,
    SandboxAbortError,
    SandboxBackupError,
    SandboxFileError,
    SandboxProtocolError,
)

__all__ = ("DirectoryBackup", "DirectoryBackupGateway", "DirectoryBackupRecord")

DIRECTORY_BACKUP_FORMAT = "tar+zstd/1"

_GATEWAY_HOST = "backups.sandbox.internal"
_ACKNOWLEDGEMENT = b"\x01"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_SAFE_INTEGER = 2**53 - 1
_SHIM_FAILURES: dict[str, BackupErrorCode] = {
    "integrity": "BACKUP_INTEGRITY",
    "notFound": "BACKUP_NOT_FOUND",
    "transfer": "BACKUP_TRANSFER",
}


class DirectoryBackupRecord(TypedDict):
    """
    What ``backup()`` returns. Store it to restore or delete the backup later.

    Records match the JS SDK's, so either SDK can restore them.
    """

    id: str
    dir: str
    size: int
    name: NotRequired[str]
    sha256: str
    format: Literal["tar+zstd/1"]


def _is_size(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= _MAX_SAFE_INTEGER
    )


def _parse_record(record: object) -> DirectoryBackupRecord:
    if not isinstance(record, Mapping):
        raise TypeError("record must be a directory backup record dict")
    valid = (
        isinstance(record.get("id"), str)
        and _UUID.fullmatch(record["id"])
        and is_absolute_path(record.get("dir"))
        and _is_size(record.get("size"))
        and isinstance(record.get("name", ""), str)
        and isinstance(record.get("sha256"), str)
        and _SHA256.fullmatch(record["sha256"])
        and record.get("format") == DIRECTORY_BACKUP_FORMAT
    )
    if not valid:
        raise ValueError(
            "record must be a directory backup record in format "
            f'"{DIRECTORY_BACKUP_FORMAT}"'
        )
    return cast(DirectoryBackupRecord, record)


def _check_dir(dir: object) -> str:
    if not isinstance(dir, str):
        raise TypeError("dir must be a string")
    if not is_absolute_path(dir):
        raise ValueError("dir must be an absolute path without NUL")
    return dir


def _check_abort(abort: object) -> None:
    if abort is not None and not isinstance(abort, asyncio.Event):
        raise TypeError("abort must be an asyncio.Event")


def _parse_backup_done(value: Any) -> dict[str, Any] | None:
    if not (
        isinstance(value, dict) and value.keys() == {"kind", "size", "sha256", "parts"}
    ):
        return None
    parts = value["parts"]
    valid = (
        value["kind"] == "done"
        and _is_size(value["size"])
        and isinstance(value["sha256"], str)
        and _SHA256.fullmatch(value["sha256"])
        and isinstance(parts, list)
        and len(parts) > 0
        and all(
            isinstance(part, dict)
            and part.keys() == {"partNumber", "etag"}
            and _is_size(part["partNumber"])
            and isinstance(part["etag"], str)
            for part in parts
        )
    )
    return value if valid else None


def _parse_restore_done(value: Any) -> dict[str, Any] | None:
    return value if value == {"kind": "done"} else None


@dataclass(frozen=True, slots=True)
class _Exchange:
    """One ``directory-backup`` command and the grant it needs."""

    command: Literal["backup", "restore"]
    request: dict[str, Any]
    path: str
    abort: asyncio.Event | None
    parse_done: Callable[[Any], dict[str, Any] | None]
    grant: Callable[[], Awaitable[None]]
    deny: Callable[[], Awaitable[None]]

    async def run(self, container: InterceptContainer) -> dict[str, Any]:
        """
        Wait for the lock, grant, acknowledge and wait for the result. Then deny
        and close stdin.

        The shim holds the backup lock until stdin closes, so this order keeps
        the deny from revoking a grant that the next operation already holds.
        An abort closes stdin and the shim removes what it partly wrote. A
        restore swaps only once acknowledged after ``verified``, and from then
        on an abort no longer stops it.
        """
        session = await ShimSession.start(
            container,
            [SHIM_PATH, "directory-backup", self.command, json.dumps(self.request)],
            {"stdin": "pipe", "stdout": "pipe", "stderr": "ignore"},
            self.abort,
            kill_on_abort=False,
        )
        control: ShimControl | None = None
        stdin: Any = None
        granting: asyncio.Future[None] | None = None
        released = False

        async def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            if granting is not None:
                await asyncio.wait({granting})
                try:
                    await self.deny()
                except Exception:
                    pass
            if stdin is not None:
                spawn(stdin.close())

        try:
            control = session.stdout_control()
            stdin = session.stdin_writer()
            if await self._read_message(control) != {"kind": "locked"}:
                raise SandboxProtocolError(
                    "sandbox-shim did not report that it holds the backup lock"
                )
            granting = asyncio.ensure_future(self.grant())
            await session.wait_for(granting)
            await session.wait_for(stdin.write(_ffi.to_uint8array(_ACKNOWLEDGEMENT)))
            if self.command == "restore":
                await self._acknowledge_verified(session, control, stdin)

            done = self.parse_done(await self._read_message(control))
            if done is None:
                raise SandboxProtocolError(
                    "sandbox-shim returned an invalid backup result"
                )
            await release()
            await control.expect_end()
            await session.expect_exit()
            control.release_lock()
            return done
        except BaseException:
            await release()
            if control is not None:
                control.discard()
            raise
        finally:
            session.finish()

    async def _acknowledge_verified(
        self, session: ShimSession, control: ShimControl, stdin: Any
    ) -> None:
        if await self._read_message(control) != {"kind": "verified"}:
            raise SandboxProtocolError("sandbox-shim did not report a verified restore")
        if self.abort is not None and self.abort.is_set():
            raise SandboxAbortError()
        session.stop_following_abort()
        await stdin.write(_ffi.to_uint8array(_ACKNOWLEDGEMENT))

    async def _read_message(self, control: ShimControl) -> dict[str, Any]:
        frame = await control.read_frame()
        if frame.kind == "file_error":
            raise SandboxFileError.from_errno(
                frame.errno, frame.detail, self.command, self.path
            )
        if frame.kind != "data":
            raise SandboxProtocolError("sandbox-shim did not return backup data")

        value = parse_json_payload(
            frame.payload, "sandbox-shim returned invalid backup data"
        )
        if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
            raise SandboxProtocolError("sandbox-shim returned invalid backup data")
        if (
            value["kind"] == "error"
            and isinstance(value.get("code"), str)
            and isinstance(value.get("detail"), str)
        ):
            raise self._shim_failure(value["code"], value["detail"])
        return value

    def _shim_failure(self, code: str, detail: str) -> Exception:
        if code == "protocol":
            return SandboxProtocolError(detail)
        if code in _SHIM_FAILURES:
            return SandboxBackupError(
                _SHIM_FAILURES[code], self.command, self.path, detail
            )
        return SandboxProtocolError(
            f'sandbox-shim returned unknown backup error code "{code}"'
        )


class DirectoryBackup:
    """
    Saves a directory of a running container to R2, and restores it.

    One backup or restore runs at a time per container, and others wait their
    turn, so ``asyncio.gather()`` over several directories works. Start the
    container first. This class never starts, retries, or times out anything.

    Parameters
    ----------
    container
        ``ctx.container``.
    gateway
        ``ctx.exports.DirectoryBackupGateway``.
    binding
        The name of the R2 bucket binding the gateway writes to.
    prefix
        A key prefix for backup objects. Empty, or ending in ``/``.
    """

    def __init__(
        self,
        container: InterceptContainer,
        gateway: Callable[[Any], Any],
        *,
        binding: str,
        prefix: str = "",
    ) -> None:
        if not isinstance(binding, str) or not isinstance(prefix, str):
            raise TypeError("binding and prefix must be strings")
        if not binding:
            raise ValueError("binding must name an R2 bucket binding")
        if prefix and not prefix.endswith("/"):
            raise ValueError('prefix must be empty or end in "/"')
        self._container = container
        self._gateway = gateway
        self._binding = binding
        self._prefix = prefix

    def __repr__(self) -> str:
        return f"<DirectoryBackup binding={self._binding!r} prefix={self._prefix!r}>"

    async def intercept(self) -> None:
        """
        Route backup traffic to the gateway, which refuses it until an operation starts.

        Call this right after starting the container, and only when the Durable
        Object also registers ``interceptAllOutboundHttp()``, which takes every
        host registered after it. Never call it while an operation may be
        running: it would replace that operation's grant.
        """
        await self._deny()

    async def backup(
        self,
        dir: str,
        *,
        name: str | None = None,
        exclude: Sequence[str] = (),
        gitignore: bool = False,
        abort: asyncio.Event | None = None,
    ) -> DirectoryBackupRecord:
        """
        Save ``dir`` as one ``tar+zstd`` object and return its record.

        Pause writers in ``dir`` first. Files that change while it is read are
        captured as they are at that moment.

        Raises
        ------
        SandboxFileError
            ``dir`` is missing or not a directory, a file can't be read, or an
            exclude pattern is invalid (``EINVAL``).
        SandboxBackupError
            ``BACKUP_TRANSFER`` when a part upload fails, or ``BACKUP_INTEGRITY``
            when R2 stored a different size than was uploaded.
        """
        _check_dir(dir)
        if name is not None and not isinstance(name, str):
            raise TypeError("name must be a string")
        if isinstance(exclude, str) or not all(
            isinstance(item, str) for item in exclude
        ):
            raise TypeError("exclude must be a sequence of strings")
        if not isinstance(gitignore, bool):
            raise TypeError("gitignore must be a boolean")
        _check_abort(abort)

        backup_id = str(uuid.uuid4())
        control = self._control(self._key(backup_id))
        upload_id, done = await self._upload(
            control,
            self._key(backup_id),
            {
                "gateway": _GATEWAY_HOST,
                "dir": dir,
                "exclude": list(exclude),
                "gitignore": gitignore,
            },
            name,
            abort,
        )
        size = await self._complete(control, upload_id, done, abort)
        if size != done["size"]:
            try:
                await control.delete_object()
            except Exception:
                pass
            raise SandboxBackupError(
                "BACKUP_INTEGRITY",
                "backup",
                dir,
                f"R2 stored {size} bytes, but the container uploaded {done['size']}",
            )

        record: DirectoryBackupRecord = {
            "id": backup_id,
            "dir": dir,
            "size": size,
            "sha256": done["sha256"],
            "format": "tar+zstd/1",
        }
        if name is not None:
            record["name"] = name
        return record

    async def restore(
        self,
        record: DirectoryBackupRecord,
        *,
        dir: str | None = None,
        abort: asyncio.Event | None = None,
    ) -> None:
        """
        Replace ``dir``, or the record's ``dir``, with the backup's contents.

        The shim extracts beside the target and swaps it in only after it
        verifies the download. A ``SandboxFileError`` or ``SandboxBackupError``
        means the target was not replaced, and so does an abort before the
        download is verified. The target's parent must exist.
        """
        parsed = _parse_record(record)
        target = parsed["dir"] if dir is None else _check_dir(dir)
        _check_abort(abort)

        key = self._key(parsed["id"])
        await _Exchange(
            command="restore",
            request={
                "gateway": _GATEWAY_HOST,
                "dir": target,
                "size": parsed["size"],
                "sha256": parsed["sha256"],
            },
            path=target,
            abort=abort,
            parse_done=_parse_restore_done,
            grant=lambda: self._register(
                {
                    "protocol_version": 1,
                    "mode": "read",
                    "binding": self._binding,
                    "key": key,
                }
            ),
            deny=self._deny,
        ).run(self._container)

    async def delete(
        self, record: DirectoryBackupRecord, *, abort: asyncio.Event | None = None
    ) -> None:
        """
        Delete the backup's object. Needs no running container.

        Deleting an object that is already gone succeeds.
        """
        parsed = _parse_record(record)
        _check_abort(abort)
        if abort is not None and abort.is_set():
            raise SandboxAbortError()
        await self._control(self._key(parsed["id"])).delete_object()

    async def _upload(
        self,
        control: Any,
        key: str,
        request: dict[str, Any],
        name: str | None,
        abort: asyncio.Event | None,
    ) -> tuple[str, dict[str, Any]]:
        """Run the shim's backup into a multipart upload, which the grant creates."""
        upload_ids: list[str] = []

        async def grant() -> None:
            upload_ids.append(await control.create_upload(name))
            await self._register(
                {
                    "protocol_version": 1,
                    "mode": "write",
                    "binding": self._binding,
                    "key": key,
                    "upload_id": upload_ids[0],
                }
            )

        try:
            done = await _Exchange(
                command="backup",
                request=request,
                path=request["dir"],
                abort=abort,
                parse_done=_parse_backup_done,
                grant=grant,
                deny=self._deny,
            ).run(self._container)
        except BaseException:
            if upload_ids:
                await _abort_upload(control, upload_ids[0])
            raise
        if not upload_ids:
            raise SandboxProtocolError("the backup finished without an upload")
        return upload_ids[0], done

    @staticmethod
    async def _complete(
        control: Any, upload_id: str, done: dict[str, Any], abort: asyncio.Event | None
    ) -> int:
        try:
            if abort is not None and abort.is_set():
                raise SandboxAbortError()
            return await control.complete_upload(
                upload_id, _ffi.to_js_object(done["parts"])
            )
        except BaseException:
            await _abort_upload(control, upload_id)
            raise

    def _key(self, backup_id: str) -> str:
        return f"{self._prefix}{backup_id}.tar.zst"

    def _control(self, key: str) -> Any:
        return self._fetcher(
            {
                "protocol_version": 1,
                "mode": "control",
                "binding": self._binding,
                "key": key,
            }
        )

    def _fetcher(self, props: dict[str, Any]) -> Any:
        return self._gateway(_ffi.to_js_object({"props": props}))

    async def _register(self, props: dict[str, Any]) -> None:
        await self._container.interceptOutboundHttp(_GATEWAY_HOST, self._fetcher(props))

    async def _deny(self) -> None:
        await self._register({"protocol_version": 1, "mode": "deny"})


async def _abort_upload(control: Any, upload_id: str) -> None:
    try:
        await control.abort_upload(upload_id)
    except Exception:
        pass


_PART_PATH = re.compile(r"/parts/([1-9][0-9]{0,8})")
_RANGE = re.compile(r"bytes=([0-9]{1,15})-([0-9]{1,15})")
_MAX_DETAIL_LENGTH = 1024
_BUCKET_METHODS = ("get", "delete", "createMultipartUpload", "resumeMultipartUpload")


def _text(status: int, detail: str) -> Any:
    return _ffi.response(detail[:_MAX_DETAIL_LENGTH], status=status)


class DirectoryBackupGateway(WorkerEntrypoint):
    """
    Moves directory backups between a container and an R2 bucket binding.

    Export it from the Worker and pass ``ctx.exports.DirectoryBackupGateway`` to
    ``DirectoryBackup``. The container reaches ``fetch()`` and can use only the
    grant its current operation holds. The other methods are for the Durable
    Object alone.
    """

    async def fetch(self, request: Any) -> Any:
        props = _ffi.to_py(self.ctx.props)
        request = _ffi.unwrap(request)
        if props.get("protocol_version") != 1:
            return _text(500, "gateway protocol is incompatible")

        match props.get("mode"):
            case "write":
                bucket = self._bucket(props["binding"])
                return await _serve_part(
                    request, bucket, props["key"], props["upload_id"]
                )
            case "read":
                return await _serve_range(
                    request, self._bucket(props["binding"]), props["key"]
                )
            case _:
                return _text(403, "no directory backup operation holds a grant")

    async def create_upload(self, name: str | None = None) -> str:
        bucket, key = self._control()
        options: dict[str, Any] = {"httpMetadata": {"contentType": "application/zstd"}}
        if name is not None:
            options["customMetadata"] = {"name": name}
        upload = await bucket.createMultipartUpload(key, _ffi.to_js_object(options))
        return upload.uploadId

    async def complete_upload(self, upload_id: str, parts: Any) -> int:
        bucket, key = self._control()
        uploaded = [
            {"partNumber": part["partNumber"], "etag": part["etag"]}
            for part in _ffi.to_py(parts)
        ]
        upload = bucket.resumeMultipartUpload(key, upload_id)
        return (await upload.complete(_ffi.to_js_object(uploaded))).size

    async def abort_upload(self, upload_id: str) -> None:
        bucket, key = self._control()
        await bucket.resumeMultipartUpload(key, upload_id).abort()

    async def delete_object(self) -> None:
        bucket, key = self._control()
        await bucket.delete(key)

    def _control(self) -> tuple[Any, str]:
        """The bucket and key of a call that only the Durable Object can make."""
        props = _ffi.to_py(self.ctx.props)
        if props.get("protocol_version") != 1 or props.get("mode") != "control":
            raise RuntimeError(
                "DirectoryBackupGateway control methods require control props"
            )
        return self._bucket(props["binding"]), props["key"]

    def _bucket(self, binding: str) -> Any:
        bucket = _ffi.unwrap(getattr(self.env, binding, None))
        if bucket is None or not all(
            callable(getattr(bucket, method, None)) for method in _BUCKET_METHODS
        ):
            raise TypeError(f"env.{binding} is not an R2 bucket binding")
        return bucket


async def _serve_part(request: Any, bucket: Any, key: str, upload_id: str) -> Any:
    """``PUT /parts/<N>``: upload one part of the granted multipart upload."""
    part = _PART_PATH.fullmatch(urlsplit(request.url).path)
    if request.method != "PUT" or part is None:
        return _text(403, "the grant allows only part uploads")
    length = _content_length(request.headers.get("content-length"))
    if length is None or _ffi.is_absent(request.body):
        return _text(411, "a part needs a Content-Length")

    try:
        stream = _ffi.fixed_length_stream(length)
        upload = bucket.resumeMultipartUpload(key, upload_id)
        uploaded, _ = await asyncio.gather(
            upload.uploadPart(int(part[1]), stream.readable),
            request.body.pipeTo(stream.writable),
        )
        return _ffi.json_response({"etag": uploaded.etag})
    except Exception as error:
        return _text(502, str(error) or "R2 rejected the part")


def _content_length(value: str | None) -> int | None:
    try:
        length = int((value or "").strip())
    except ValueError:
        return None
    return length if 0 < length <= _MAX_SAFE_INTEGER else None


async def _serve_range(request: Any, bucket: Any, key: str) -> Any:
    """``GET /object`` with ``Range: bytes=a-b``: read a range of the granted object."""
    byte_range = _RANGE.fullmatch(request.headers.get("range") or "")
    if (
        request.method != "GET"
        or urlsplit(request.url).path != "/object"
        or byte_range is None
    ):
        return _text(403, "the grant allows only ranged reads of the backup")
    offset, last = int(byte_range[1]), int(byte_range[2])
    if last < offset:
        return _text(416, "invalid range")

    try:
        range_options = {"range": {"offset": offset, "length": last - offset + 1}}
        found = await bucket.get(key, _ffi.to_js_object(range_options))
        if _ffi.is_absent(found):
            return _text(404, "the backup object does not exist")
        end = min(last, found.size - 1)
        if end < offset:
            return _text(416, "the range starts past the end of the object")
        return _ffi.response(
            found.body,
            status=206,
            headers={"content-range": f"bytes {offset}-{end}/{found.size}"},
        )
    except Exception as error:
        return _text(502, str(error) or "R2 rejected the range")
