---
title: "Back up and restore a sandbox directory to R2"
description: "Use sandboxes.DirectoryBackup and DirectoryBackupGateway to save a container directory to an R2 bucket as one tar+zstd object, restore it and delete it."
---

A container's disk is gone once the container stops. To keep a directory, save
it with [`DirectoryBackup`](api.md#directorybackup). It packs the directory
into one `tar+zstd` object in an R2 bucket and returns a record. Store the
record, and pass it to `restore()` when a new container needs the files back.

The container never gets access to the bucket. It sends the archive to
[`DirectoryBackupGateway`](api.md#directorybackupgateway) in your Worker, which
writes only the object the current backup created.

## 1. Add an R2 bucket

```sh
npx wrangler r2 bucket create sandbox-backups
```

Bind it in your config:

=== "wrangler.jsonc"

    ```jsonc
    {
      "r2_buckets": [{ "binding": "BACKUPS", "bucket_name": "sandbox-backups" }]
    }
    ```

=== "wrangler.toml"

    ```toml
    [[r2_buckets]]
    binding = "BACKUPS"
    bucket_name = "sandbox-backups"
    ```

=== "cloudflare.config.py"

    ```python
    from wynd.config import bindings

    env={"BACKUPS": bindings.R2(name="sandbox-backups")},
    ```

## 2. Export the gateway

`DirectoryBackup` reaches the gateway through
`self.ctx.exports.DirectoryBackupGateway`. The runtime only fills in
`ctx.exports` with classes your entry module defines or imports, so import it
in `src/entry.py`:

```python title="src/entry.py"
from sandboxes import DirectoryBackup, DirectoryBackupGateway  # noqa: F401
```

## 3. Save and restore

```python title="src/entry.py"
import json

from sandboxes import DirectoryBackup, DirectoryBackupGateway  # noqa: F401
from workers import DurableObject


class Sandbox(DurableObject):
    def backups(self) -> DirectoryBackup:
        container = self.ctx.container
        if not container.running:
            container.start()
        return DirectoryBackup(
            container, self.ctx.exports.DirectoryBackupGateway, binding="BACKUPS"
        )

    async def save(self) -> None:
        record = await self.backups().backup("/workspace", gitignore=True)
        await self.ctx.storage.put("backup", json.dumps(record))

    async def load(self) -> None:
        stored = await self.ctx.storage.get("backup")
        if stored is not None:
            await self.backups().restore(json.loads(stored))
```

`binding` is the binding name from `wrangler.jsonc`, as a string.

`backup()` returns a [`DirectoryBackupRecord`](api.md#directorybackuprecord):

```python
{
    "id": "0c5e7f0a-3b1d-4c7e-9a8f-2d6b1e4f5a90",
    "dir": "/workspace",
    "size": 48213,
    "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "format": "tar+zstd/1",
}
```

The object's key in R2 is `<id>.tar.zst`. With `gitignore=True`, paths that
`.gitignore` files in the directory ignore are left out. `exclude` takes more
patterns to leave out.

Stop programs that write to the directory before you call `backup()`. A file
that changes while the shim reads it is saved as it was at that moment, so the
archive can mix old and new files.

## Restore to another path

The examples below use `backups = self.backups()` and a `record` from
`backup()`.

`restore()` replaces the record's `dir` by default. Pass `dir` to restore
somewhere else:

```python
await backups.restore(record, dir="/workspace-copy")
```

The parent of the target, `/` here, must exist. The shim extracts next to the
target and checks the download's size and SHA-256 against the record. It
replaces the target only when both match. If `restore()` raises
[`SandboxFileError`](api.md#errors) or
[`SandboxBackupError`](api.md#errors), the target is unchanged.

## Delete a backup

```python
await backups.delete(record)
```

Deleting doesn't need a running container. Deleting a backup that is already
gone succeeds.

## Organize backups with a prefix

`prefix` puts every object under a key prefix. It must end in `/`:

```python
DirectoryBackup(container, gateway, binding="BACKUPS", prefix="user-42/")
```

The object is then `user-42/<id>.tar.zst`. Restore and delete with the same
prefix, because the record doesn't store it.

## Run several backups

One backup or restore runs at a time per container. Others wait their turn, so
you can start several at once:

```python
import asyncio

records = await asyncio.gather(
    backups.backup("/workspace"),
    backups.backup("/home/app"),
)
```

## Use with a catch-all intercept

If the Durable Object registers `interceptAllOutboundHttp()` on the container,
call `intercept()` right after the container starts and before the catch-all:

```python
container.start()
await backups.intercept()
```

The catch-all takes every host registered after it, so the gateway has to be
registered first. Don't call `intercept()` while a backup or restore runs. It
replaces that operation's access to the bucket and the operation fails.

## Handle errors

| Error                                      | When                                                                                 |
| ------------------------------------------ | ------------------------------------------------------------------------------------ |
| `SandboxFileError`                         | The directory is missing or isn't a directory, a file can't be read, or an `exclude` pattern is invalid (`code` `"EINVAL"`). |
| `SandboxBackupError`, `"BACKUP_NOT_FOUND"` | `restore()` found no object for the record.                                          |
| `SandboxBackupError`, `"BACKUP_INTEGRITY"` | The object's size or SHA-256 doesn't match the record.                               |
| `SandboxBackupError`, `"BACKUP_TRANSFER"`  | An upload or download between the container and R2 failed.                          |

A failed backup aborts its upload, so it leaves no partial object in the
bucket.
