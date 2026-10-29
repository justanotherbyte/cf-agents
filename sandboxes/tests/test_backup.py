import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from test_setup import Container, data, errno_frame

from sandboxes import (
    DirectoryBackup,
    DirectoryBackupGateway,
    SandboxAbortError,
    SandboxBackupError,
    SandboxFileError,
)

SHA = "a" * 64
RECORD: Any = {
    "id": "12345678-1234-1234-1234-123456789abc",
    "dir": "/work",
    "size": 10,
    "sha256": SHA,
    "format": "tar+zstd/1",
}


def message(value: Any) -> bytes:
    return data(json.dumps(value).encode())


class Gateway:
    """``ctx.exports.DirectoryBackupGateway``: control stubs record their calls."""

    def __init__(self, log: list[Any], stored_size: int = 10) -> None:
        self.log = log
        self.stored_size = stored_size

    def __call__(self, options: dict[str, Any]) -> Any:
        if options["props"]["mode"] != "control":
            return options
        log, stored_size = self.log, self.stored_size

        class Control:
            async def create_upload(self, name: str | None) -> str:
                log.append(("create_upload", name))
                return "upload-1"

            async def complete_upload(self, upload_id: str, parts: Any) -> int:
                log.append(("complete_upload", upload_id, parts))
                return stored_size

            async def abort_upload(self, upload_id: str) -> None:
                log.append(("abort_upload", upload_id))

            async def delete_object(self) -> None:
                log.append(("delete_object", options["props"]["key"]))

        return Control()


DONE = {
    "kind": "done",
    "size": 10,
    "sha256": SHA,
    "parts": [{"partNumber": 1, "etag": "e"}],
}


async def test_backup_grants_after_the_lock_and_denies_before_closing_stdin() -> None:
    container = Container()
    process = container.script(stdout=[message({"kind": "locked"}), message(DONE)])
    backups = DirectoryBackup(
        container, Gateway(container.log), binding="BACKUPS", prefix="p/"
    )

    record = await backups.backup("/work", name="nightly", exclude=["node_modules"])
    await asyncio.sleep(0)

    cmd = container.calls[0][0]
    assert cmd[1:3] == ["directory-backup", "backup"]
    assert json.loads(cmd[3]) == {
        "gateway": "backups.sandbox.internal",
        "dir": "/work",
        "exclude": ["node_modules"],
        "gitignore": False,
    }
    assert "signal" not in container.calls[0][1]
    assert container.log[1:] == [
        ("create_upload", "nightly"),
        ("intercept", "backups.sandbox.internal", "write"),
        ("write", b"\x01"),
        ("intercept", "backups.sandbox.internal", "deny"),
        ("close",),
        ("complete_upload", "upload-1", DONE["parts"]),
    ]
    assert container.intercepts[0][1]["props"]["key"] == f"p/{record['id']}.tar.zst"
    assert process.stdin.closed
    assert record == {
        "id": record["id"],
        "dir": "/work",
        "size": 10,
        "sha256": SHA,
        "format": "tar+zstd/1",
        "name": "nightly",
    }


async def test_backup_size_mismatch_deletes_the_object() -> None:
    container = Container()
    container.script(stdout=[message({"kind": "locked"}), message(DONE)])
    backups = DirectoryBackup(
        container, Gateway(container.log, stored_size=9), binding="B"
    )

    with pytest.raises(SandboxBackupError) as caught:
        await backups.backup("/work")

    assert caught.value.code == "BACKUP_INTEGRITY"
    assert container.log[-1][0] == "delete_object"


async def test_backup_shim_failure_aborts_the_upload() -> None:
    container = Container()
    container.script(
        stdout=[
            message({"kind": "locked"}),
            message({"kind": "error", "code": "transfer", "detail": "R2 said no"}),
        ]
    )
    backups = DirectoryBackup(container, Gateway(container.log), binding="B")

    with pytest.raises(
        SandboxBackupError, match="backup '/work': R2 said no"
    ) as caught:
        await backups.backup("/work")

    assert caught.value.code == "BACKUP_TRANSFER"
    assert ("abort_upload", "upload-1") in container.log


async def test_backup_of_a_missing_directory_never_grants() -> None:
    container = Container()
    container.script(stdout=[errno_frame(2, "no such directory")])
    backups = DirectoryBackup(container, Gateway(container.log), binding="B")

    with pytest.raises(SandboxFileError) as caught:
        await backups.backup("/missing")

    assert (caught.value.code, caught.value.operation) == ("ENOENT", "backup")
    assert container.intercepts == []


async def test_restore_acknowledges_twice() -> None:
    container = Container()
    process = container.script(
        stdout=[
            message({"kind": "locked"}),
            message({"kind": "verified"}),
            message({"kind": "done"}),
        ]
    )
    backups = DirectoryBackup(container, Gateway(container.log), binding="B")

    await backups.restore(RECORD, dir="/elsewhere")

    request = json.loads(container.calls[0][0][3])
    assert request == {
        "gateway": "backups.sandbox.internal",
        "dir": "/elsewhere",
        "size": 10,
        "sha256": SHA,
    }
    assert container.intercepts[0][1]["props"]["mode"] == "read"
    assert process.stdin.written == b"\x01\x01"


async def test_restore_abort_before_verified_skips_the_swap() -> None:
    abort = asyncio.Event()
    verified = asyncio.get_running_loop().create_future()
    container = Container()
    process = container.script(stdout=[message({"kind": "locked"}), verified])
    backups = DirectoryBackup(container, Gateway(container.log), binding="B")

    task = asyncio.ensure_future(backups.restore(RECORD, abort=abort))
    await asyncio.sleep(0.01)
    abort.set()

    with pytest.raises(SandboxAbortError):
        await task
    await asyncio.sleep(0)
    assert process.stdin.written == b"\x01"
    assert process.stdin.closed
    assert process.killed is None
    assert ("intercept", "backups.sandbox.internal", "deny") in container.log


async def test_delete_and_intercept() -> None:
    container = Container()
    backups = DirectoryBackup(
        container, Gateway(container.log), binding="B", prefix="x/"
    )

    await backups.delete(RECORD)
    await backups.intercept()

    assert container.log == [
        ("delete_object", f"x/{RECORD['id']}.tar.zst"),
        ("intercept", "backups.sandbox.internal", "deny"),
    ]


@pytest.mark.parametrize(
    ("record", "error"),
    [
        ("nope", TypeError),
        ({**RECORD, "id": "x"}, ValueError),
        ({**RECORD, "size": 0}, ValueError),
        ({**RECORD, "size": True}, ValueError),
        ({**RECORD, "format": "zip"}, ValueError),
        ({**RECORD, "dir": "rel"}, ValueError),
    ],
)
async def test_invalid_records(record: Any, error: type[Exception]) -> None:
    backups = DirectoryBackup(Container(), Gateway([]), binding="B")

    with pytest.raises(error):
        await backups.delete(record)


@pytest.mark.parametrize(("binding", "prefix"), [("", ""), ("B", "no-slash")])
def test_invalid_storage(binding: str, prefix: str) -> None:
    with pytest.raises(ValueError):
        DirectoryBackup(Container(), Gateway([]), binding=binding, prefix=prefix)


class Bucket:
    def __init__(self, size: int) -> None:
        self.size = size
        self.requests: list[Any] = []

    async def get(self, key: str, options: dict[str, Any]) -> Any:
        self.requests.append((key, options))
        return SimpleNamespace(size=self.size, body="bytes")

    async def delete(self, key: str) -> None: ...

    def createMultipartUpload(self) -> None: ...

    def resumeMultipartUpload(self) -> None: ...


def gateway_for(props: dict[str, Any], bucket: Bucket) -> DirectoryBackupGateway:
    return DirectoryBackupGateway(
        SimpleNamespace(props=props), SimpleNamespace(BACKUPS=bucket)
    )


def get(path: str, byte_range: str | None) -> Any:
    headers = {} if byte_range is None else {"range": byte_range}
    return SimpleNamespace(
        method="GET", url=f"http://backups.sandbox.internal{path}", headers=headers
    )


async def test_gateway_serves_ranges_of_the_granted_object_only() -> None:
    bucket = Bucket(size=10)
    props = {"protocol_version": 1, "mode": "read", "binding": "BACKUPS", "key": "k"}
    gateway = gateway_for(props, bucket)

    ok = await gateway.fetch(get("/object", "bytes=4-99"))
    past_end = await gateway.fetch(get("/object", "bytes=10-12"))
    wrong_path = await gateway.fetch(get("/other", "bytes=0-1"))
    denied = await gateway_for({"protocol_version": 1, "mode": "deny"}, bucket).fetch(
        get("/object", "bytes=0-1")
    )

    assert (ok.status, ok.headers) == (206, {"content-range": "bytes 4-9/10"})
    assert bucket.requests[0] == ("k", {"range": {"offset": 4, "length": 96}})
    assert past_end.status == 416
    assert wrong_path.status == 403
    assert denied.status == 403


async def test_gateway_control_methods_need_control_props() -> None:
    gateway = gateway_for(
        {"protocol_version": 1, "mode": "read", "binding": "BACKUPS", "key": "k"},
        Bucket(1),
    )

    with pytest.raises(RuntimeError):
        await gateway.delete_object()
