---
title: "crosswind-sandboxes API reference"
description: "Every class, method, type and error in crosswind-sandboxes: Files, S3Mount, S3Gateway, DirectoryBackup, DirectoryBackupGateway and the SandboxError hierarchy."
---

Everything on this page imports from `sandboxes`. All methods are coroutines.

These rules apply to every class:

- The container must be running. No class starts, stops or restarts it.
- Pass the container as `self.ctx.container` from inside the Durable Object
  that owns it.
- Options are keyword-only. A value of the wrong type raises `TypeError` and an
  invalid value raises `ValueError`, both before anything runs in the
  container.
- `abort` takes an `asyncio.Event`. Setting it stops the call, which raises
  [`SandboxAbortError`](#errors). An event can't cross RPC, so create it in
  the Durable Object.
- Any call can raise [`SandboxProtocolError`](#errors) if the shim in the image
  doesn't match the package.

## Files

```python
Files(container)
```

Reads and changes files in a running container. `container` is
`self.ctx.container`, or any object with a compatible `exec()`.

| Method                                                  | Returns                                                 |
| ------------------------------------------------------- | ------------------------------------------------------- |
| `read_file(path, /, *, cwd, user, abort)`               | JavaScript `Response` that streams the file             |
| `write_file(path, content, /, *, cwd, user, abort)`     | `None`                                                  |
| `stat(path, /, *, cwd, user, abort)`                    | [`SandboxFileStat`](#sandboxfilestat)                   |
| `lstat(path, /, *, cwd, user, abort)`                   | [`SandboxFileStat`](#sandboxfilestat)                   |
| `read_directory(path, /, *, cwd, user, abort)`          | [`list[SandboxDirectoryEntry]`](#sandboxdirectoryentry) |
| `mkdir(path, /, *, recursive=False, cwd, user, abort)`  | `None`                                                  |
| `rename(source, destination, /, *, cwd, user, abort)`   | `None`                                                  |
| `remove(path, /, *, recursive=False, force=False, cwd, user, abort)` | `None`                                     |

Every method takes these options:

| Option  | Type                    | Default | Description                                                                  |
| ------- | ----------------------- | ------- | ---------------------------------------------------------------------------- |
| `cwd`   | `str \| None`           | `None`  | Absolute directory that relative paths join onto. A relative path without it raises `ValueError`. |
| `user`  | `str \| None`           | `None`  | Numeric `"uid:gid"` to run as. `None` runs as the container's default user.  |
| `abort` | `asyncio.Event \| None` | `None`  | Set it to cancel. The shim is killed.                                        |

Paths can't be empty or contain NUL characters.

Every method raises [`SandboxFileError`](#errors) when Linux rejects the
operation. Its `code` is the errno name, such as `"ENOENT"`.

### `read_file`

Streams a file's bytes. Returns a JavaScript `Response`. Read it with
`await response.text()` or `await response.bytes()`, or return it from a
Worker. A missing or unreadable file raises `SandboxFileError` from the call.
An error after the file opened errors the response body, with a JavaScript
`Error` whose `name` is the Python exception's class name.

```python
text = await (await files.read_file("/workspace/main.py")).text()
```

### `write_file`

Creates or truncates `path` and writes `content` into it. The parent directory
must exist.

| `content` type                   | Written as                |
| -------------------------------- | ------------------------- |
| `str`                            | UTF-8 bytes               |
| `bytes`, `bytearray`, `memoryview` | As is                   |
| Async iterable of bytes-like     | Each chunk, streamed      |
| JavaScript `ReadableStream`      | Each `Uint8Array` chunk, streamed |

Any other type raises `TypeError`. The file opens before `content` is read, so
a failure partway leaves the file created, truncated or partly written.

```python
await files.write_file("/workspace/main.py", "print('hi')\n")
```

### `stat` and `lstat`

Return a [`SandboxFileStat`](#sandboxfilestat). `stat()` follows a final
symbolic link. `lstat()` describes the link itself.

```python
size = (await files.stat("/workspace/main.py")).size
```

### `read_directory`

Lists the immediate entries of a directory, in the order the filesystem
returns them.

```python
names = [entry.name for entry in await files.read_directory("/workspace")]
```

### `mkdir`

Creates a directory. With `recursive=True`, also creates missing parents.
Without it, an existing directory raises `SandboxFileError` with `"EEXIST"`.

```python
await files.mkdir("/workspace/out/logs", recursive=True)
```

### `rename`

Renames `source` to `destination` with Linux `rename` rules. Moving across
filesystems raises `SandboxFileError` with `"EXDEV"`. Nothing is copied
instead. On failure, the error's `destination` attribute holds the
destination.

```python
await files.rename("/workspace/main.py", "/workspace/app.py")
```

### `remove`

Removes a file or symbolic link. `recursive=True` also removes a directory and
everything in it. `force=True` succeeds when the path doesn't exist, and still
raises every other error.

```python
await files.remove("/workspace/out", recursive=True, force=True)
```

## SandboxFileStat

A frozen dataclass that `stat()` and `lstat()` return.

| Attribute     | Type                                  | Description                               |
| ------------- | ------------------------------------- | ----------------------------------------- |
| `type`        | [`SandboxFileType`](#sandboxfiletype) | The kind of path.                         |
| `size`        | `int`                                 | Size in bytes.                            |
| `mode`        | `int`                                 | Permission bits.                          |
| `uid`         | `int`                                 | Owner's user ID.                          |
| `gid`         | `int`                                 | Owner's group ID.                         |
| `accessed_at` | `datetime`                            | Last access, in UTC.                      |
| `modified_at` | `datetime`                            | Last change to the contents, in UTC.      |
| `changed_at`  | `datetime`                            | Last change to the metadata, in UTC.      |

## SandboxDirectoryEntry

A frozen dataclass. `read_directory()` returns a list of them.

| Attribute | Type                                  | Description                                                   |
| --------- | ------------------------------------- | ------------------------------------------------------------- |
| `name`    | `str`                                 | The entry's name, without its directory.                      |
| `type`    | [`SandboxFileType`](#sandboxfiletype) | The kind of entry. A symbolic link is `"symlink"`, not its target's type. |

## SandboxFileType

A `Literal` of `"file"`, `"directory"`, `"symlink"`, `"block_device"`,
`"character_device"`, `"fifo"` and `"socket"`.

## FileContent

The type alias for `write_file()`'s `content`: `str`, bytes-like, an async
iterable of bytes, or a JavaScript `ReadableStream`.

## S3Mount

```python
S3Mount(container, gateway)
```

Mounts an S3-compatible bucket in a running container with `s3fs`. The image
must contain `s3fs`.

| Parameter   | Description                                                                            |
| ----------- | -------------------------------------------------------------------------------------- |
| `container` | `self.ctx.container`. It must have `exec()` and `interceptOutboundHttp()`.             |
| `gateway`   | `self.ctx.exports.S3Gateway`. [`S3Gateway`](#s3gateway) must be imported in the entry module. |

Call `mount()` before registering `interceptAllOutboundHttp()` on the
container, which takes every host registered after it.

### `mount`

```python
await mounts.mount(mount_path, *, source, access, key_prefix=None, s3fs_options=None, abort=None)
```

Mounts `source` at `mount_path`. A mount with the same settings already at the
path is reused, and leftover state from an earlier mount is repaired. Returns
`None`.

| Parameter      | Type                                        | Default    | Description                                                                         |
| -------------- | ------------------------------------------- | ---------- | ----------------------------------------------------------------------------------- |
| `mount_path`   | `str`                                       | required   | Absolute and normalized: not `/`, no trailing `/`, no `.` or `..` segments.         |
| `source`       | [`S3Source`](#s3source)                     | required   | The bucket and its credentials.                                                     |
| `access`       | `"read-only" \| "read-write"`               | required   | With `"read-only"`, the gateway refuses every write.                                |
| `key_prefix`   | `str \| None`                               | `None`     | Mount only keys under this prefix. Can't start with `/`. A trailing `/` is added.   |
| `s3fs_options` | `Mapping[str, str \| float \| bool] \| None` | `None`     | Extra `s3fs` options. `True` adds a flag, `False` drops it, other values add `name=value`. |
| `abort`        | `asyncio.Event \| None`                     | `None`     | Set it to cancel.                                                                    |

Raises:

- `ValueError` when `mount_path` overlaps `/proc/self/mountinfo`,
  `/run/sandbox/s3-mounts` or `/usr/local/bin/sandbox-shim`, or when
  `s3fs_options` names an option the package sets itself: `ahbe_conf`,
  `allow_other`, `compat_dir`, `credlib`, `ecs`, `endpoint`, `f`, `fg`,
  `foreground`, `fsname`, `host`, `iam_role`, `ibm_iam_auth`, `logfile`,
  `nomixupload`, `noproxy`, `passwd_file`, `profile`, `proxy`,
  `proxy_cred_file`, `public_bucket`, `ro`, `rw`, `subtype`,
  `use_path_request_style`, `use_proxy`, `use_session_token` or `url`.
- [`SandboxS3MountError`](#errors) with `"S3_MOUNT_CONFLICT"` when a mount with
  different settings holds the path, `"S3_MOUNT_BUSY"`,
  `"S3_MOUNT_INCOMPATIBLE"` or `"S3_MOUNT_FAILED"`.

### `inspect`

```python
await mounts.inspect(mount_path, *, abort=None)
```

Returns an [`S3MountInspection`](#s3mountinspection) for the path without
changing it. Waits first for any `mount()` or `unmount()` running on the path.

### `unmount`

```python
await mounts.unmount(mount_path, *, abort=None)
```

Makes the gateway refuse the mount's requests, then unmounts. Unmounting a
path with nothing mounted succeeds. Raises
[`SandboxS3MountError`](#errors) with `"S3_MOUNT_BUSY"` when a process still
uses the path. The gateway keeps refusing requests in that case.

## S3Source

A `TypedDict` passed as `mount(source=...)`.

| Key           | Type                                | Description                                                                 |
| ------------- | ----------------------------------- | --------------------------------------------------------------------------- |
| `type`        | `"s3"`                              | Always `"s3"`.                                                              |
| `endpoint`    | `str`                               | HTTP or HTTPS origin, such as `"https://<ACCOUNT_ID>.r2.cloudflarestorage.com"`. No path, query or credentials. |
| `region`      | `str`                               | Signing region. R2 uses `"auto"`.                                           |
| `bucket`      | `str`                               | Bucket name. Can't start with `-` or contain `/` or `:`.                    |
| `credentials` | [`S3Credentials`](#s3credentials)   | Static keys or a provider.                                                  |

## S3Credentials

`StaticCredentials | ProviderCredentials`.

`StaticCredentials` holds fixed keys:

| Key                 | Type        | Required | Description                     |
| ------------------- | ----------- | -------- | ------------------------------- |
| `type`              | `"static"`  | Yes      | Always `"static"`.              |
| `access_key_id`     | `str`       | Yes      | The access key ID.              |
| `secret_access_key` | `str`       | Yes      | The secret access key.          |
| `session_token`     | `str`       | No       | For temporary credentials.      |

`ProviderCredentials` fetches keys before each request:

| Key       | Type          | Description                                                    |
| --------- | ------------- | -------------------------------------------------------------- |
| `type`    | `"provider"`  | Always `"provider"`.                                           |
| `fetcher` | Fetcher       | Any binding with `fetch()`, such as a service binding.         |

The gateway sends `GET https://credentials.sandbox.internal/` to `fetcher`. It
must answer `200` with JSON of at most 16,384 bytes, holding `accessKeyId`,
`secretAccessKey`, an optional `sessionToken`, and `expiresAt` in milliseconds
since the Unix epoch. Expired keys fail the request.

## S3MountInspection

A `TypedDict` that `inspect()` returns.

| Key          | Type   | Present                    | Description                                       |
| ------------ | ------ | -------------------------- | ------------------------------------------------- |
| `mount_path` | `str`  | Always                     | The path inspected.                               |
| `attachment` | `dict` | Always                     | What holds the path. See below.                   |
| `fuse`       | `dict` | `"managed"` only           | `status`: `"connected"`, `"disconnected"` or `"indeterminate"` with a `detail`. |
| `gateway`    | `dict` | `"managed"` and `"stale"`  | Whether a test request through the gateway worked. See below. |

`attachment["status"]` is one of:

| Status           | Meaning                                                                    |
| ---------------- | -------------------------------------------------------------------------- |
| `"absent"`       | Nothing is mounted.                                                        |
| `"unmanaged"`    | Another filesystem. `filesystem_type` names it.                            |
| `"incompatible"` | A mount made by another shim protocol version.                             |
| `"stale"`        | A mount this package made that no longer works. Has `configuration`.       |
| `"managed"`      | A mount this package made. Has `configuration`, an [`S3MountConfiguration`](#s3mountconfiguration). |

`gateway["status"]` is one of:

- `"unreachable"`, with `detail`.
- `"error"`, with `reason` (`"credential-provider"`, `"protocol"` or
  `"internal"`) and `detail`.
- `"reachable"`, with `upstream`. `upstream["status"]` is `"usable"`,
  `"unavailable"` with `detail`, or `"rejected"` with `detail` and `reason`
  (`"credentials"`, `"access"`, `"not-found"` or `"other"`).

## S3MountConfiguration

A `TypedDict` of a mount's settings as the container records them, without
credentials: `source` (`type`, `endpoint`, `region`, `bucket`), `key_prefix`
if set, `access`, and `s3fs_options` as a list of `{"name", "value"}` dicts.

## S3MountAccess

`Literal["read-only", "read-write"]`.

## S3Gateway

A `WorkerEntrypoint` that checks and signs the container's storage requests.
Import it in your entry module so `self.ctx.exports.S3Gateway` exists, and pass
that to [`S3Mount`](#s3mount). You never call it yourself.

It allows only the object and list operations `s3fs` uses, inside the mounted
bucket and key prefix, and refuses writes to a read-only mount.

## DirectoryBackup

```python
DirectoryBackup(container, gateway, *, binding, prefix="")
```

Saves a directory of a running container to R2 and restores it. One backup or
restore runs at a time per container, and others wait, so `asyncio.gather()`
over several calls works. No call retries or times out.

| Parameter   | Type   | Default  | Description                                                                         |
| ----------- | ------ | -------- | ----------------------------------------------------------------------------------- |
| `container` |        | required | `self.ctx.container`.                                                               |
| `gateway`   |        | required | `self.ctx.exports.DirectoryBackupGateway`. [`DirectoryBackupGateway`](#directorybackupgateway) must be imported in the entry module. |
| `binding`   | `str`  | required | Name of the R2 bucket binding, such as `"BACKUPS"`. Empty raises `ValueError`.      |
| `prefix`    | `str`  | `""`     | Key prefix for backup objects. Must be empty or end in `/`.                         |

Objects are stored at `<prefix><id>.tar.zst` with content type
`application/zstd`.

### `backup`

```python
await backups.backup(dir, *, name=None, exclude=(), gitignore=False, abort=None)
```

Saves `dir` as one `tar+zstd` object and returns a
[`DirectoryBackupRecord`](#directorybackuprecord). Stop writers in `dir` first.
A file that changes during the backup is saved as it was when read.

| Parameter   | Type                    | Default  | Description                                                     |
| ----------- | ----------------------- | -------- | --------------------------------------------------------------- |
| `dir`       | `str`                   | required | Absolute directory to save.                                     |
| `name`      | `str \| None`           | `None`   | Label stored in the record and in the object's custom metadata. |
| `exclude`   | `Sequence[str]`         | `()`     | Patterns of paths to leave out. A bare `str` raises `TypeError`. |
| `gitignore` | `bool`                  | `False`  | Also leave out paths that `.gitignore` files in `dir` ignore.   |
| `abort`     | `asyncio.Event \| None` | `None`   | Set it to cancel. The partial upload is aborted.                |

Raises:

- [`SandboxFileError`](#errors) when `dir` is missing or not a directory, a
  file can't be read, or an `exclude` pattern is invalid (`"EINVAL"`).
- [`SandboxBackupError`](#errors) with `"BACKUP_TRANSFER"` when an upload
  fails, or `"BACKUP_INTEGRITY"` when R2 stored a different size than the
  container sent. The object is deleted in that case.

### `restore`

```python
await backups.restore(record, *, dir=None, abort=None)
```

Replaces `dir`, or the record's `dir`, with the backup's contents. The target's
parent must exist. The shim extracts beside the target and swaps it in only
after the download's size and SHA-256 match the record. Once that check
passes, setting `abort` no longer stops the restore.

Raises:

- `ValueError` when `record` isn't a valid record.
- [`SandboxFileError`](#errors) when the target's parent is missing or a file
  can't be written.
- [`SandboxBackupError`](#errors) with `"BACKUP_NOT_FOUND"`,
  `"BACKUP_INTEGRITY"` or `"BACKUP_TRANSFER"`.

After any of these errors, or an abort before the check, the target is
unchanged.

### `delete`

```python
await backups.delete(record, *, abort=None)
```

Deletes the backup's object. Needs no running container. Deleting an object
that is already gone succeeds. Raises `ValueError` when `record` isn't a valid
record.

### `intercept`

```python
await backups.intercept()
```

Registers the gateway for backup traffic, refusing it until an operation
starts. Call it right after starting the container, and only when the Durable
Object also registers `interceptAllOutboundHttp()`. Calling it while an
operation runs makes that operation fail.

## DirectoryBackupRecord

A `TypedDict` that `backup()` returns. It holds plain JSON values, so
`json.dumps()` and Durable Object storage accept it. Records from the
TypeScript SDK work too.

| Key      | Type           | Description                                     |
| -------- | -------------- | ----------------------------------------------- |
| `id`     | `str`          | UUID that names the object.                     |
| `dir`    | `str`          | The directory that was saved.                   |
| `size`   | `int`          | Object size in bytes.                           |
| `name`   | `str`          | The `name` passed to `backup()`. Only if set.   |
| `sha256` | `str`          | Hex SHA-256 of the object.                      |
| `format` | `"tar+zstd/1"` | Archive format.                                 |

## DirectoryBackupGateway

A `WorkerEntrypoint` that moves backup bytes between the container and the R2
binding. Import it in your entry module so
`self.ctx.exports.DirectoryBackupGateway` exists, and pass that to
[`DirectoryBackup`](#directorybackup). You never call it yourself.

The container can only upload parts of the backup that is running, or read the
object being restored. Requests outside that grant get `403`.

## Errors

Every exception derives from `SandboxError`. Catch it to handle every failure
from this package.

```text
SandboxError
├── SandboxAbortError
├── SandboxFileError
├── SandboxProtocolError
├── SandboxS3MountError
└── SandboxBackupError
```

| Exception              | Raised when                                                       | Attributes                                                         |
| ---------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------ |
| `SandboxAbortError`    | The `abort` event was set before the call finished.               | None.                                                              |
| `SandboxFileError`     | Linux rejected a file operation.                                  | `code` (errno name, or `"UNKNOWN"`), `operation`, `path`, `destination` (`rename` only), `detail`. |
| `SandboxProtocolError` | The package and the shim couldn't complete their exchange, usually because the image's shim doesn't match this package's version. | `code` (`"SANDBOX_PROTOCOL_ERROR"`), `detail`.                     |
| `SandboxS3MountError`  | A mount, inspect or unmount failed.                               | `code` (`"S3_MOUNT_BUSY"`, `"S3_MOUNT_CONFLICT"`, `"S3_MOUNT_FAILED"`, `"S3_MOUNT_INCOMPATIBLE"`), `operation`, `path`, `detail`. |
| `SandboxBackupError`   | A backup is missing, changed or failed to transfer.               | `code` (`"BACKUP_NOT_FOUND"`, `"BACKUP_INTEGRITY"`, `"BACKUP_TRANSFER"`), `operation`, `path`, `detail`. |

`operation` is the method name, such as `"read_file"` or `"mount"`.

```python
from sandboxes import SandboxFileError

try:
    await files.stat("/missing")
except SandboxFileError as error:
    assert error.code == "ENOENT"
```

## Differences from the TypeScript SDK

- Names are `snake_case`, and option objects are keyword-only arguments.
- `AbortSignal` is an `asyncio.Event` passed as `abort`.
- Invalid values raise `ValueError`. TypeScript raises `TypeError` for both
  wrong types and invalid values.
- Errors are exception classes. Check them with `isinstance` or `except`.
  There is no `is()` helper.
- `stat()` returns a dataclass with an `int` size and UTC `datetime` times.
  File types are `"block_device"` and `"character_device"`.
- The Worker doesn't need `nodejs_compat`.
