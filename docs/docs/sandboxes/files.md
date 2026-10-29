---
title: "Read and write files in a Cloudflare sandbox"
description: "Use sandboxes.Files to read, write, stat, list, rename and remove files in a running container, and handle the errors Linux returns."
---

[`Files`](api.md#files) works on the filesystem of a running container. Create
one from `self.ctx.container` inside your Durable Object. The examples on this
page are methods of the `Sandbox` class from
[Getting started](getting-started.md), and they assume the container is
already running.

```python
from sandboxes import Files

files = Files(self.ctx.container)
```

## Write a file

```python
await files.write_file("/workspace/main.py", "print('hi')\n")
```

`write_file()` creates the file or truncates an existing one. A `str` is
written as UTF-8. `bytes`, `bytearray` and `memoryview` are written as they
are. The parent directory must exist, or the call raises
[`SandboxFileError`](api.md#errors) with `code` `"ENOENT"`.

To write a large file without holding it in memory, pass an async iterable of
bytes or a JavaScript `ReadableStream`, such as a request body:

```python
async def numbers():
    for i in range(100_000):
        yield f"{i}\n".encode()


await files.write_file("/workspace/numbers.txt", numbers())
```

The file is opened before any content is read. If the content fails halfway,
the file is left partly written.

## Read a file

```python
response = await files.read_file("/workspace/main.py")
source = await response.text()
```

`read_file()` returns a JavaScript `Response` that streams the file. Use
`.text()`, `.bytes()` or `.json()` to read it, or return it from your Worker to
stream it to the client. A missing file raises `SandboxFileError` from
`read_file()` itself. A read error after the file opened errors the response
body instead.

## List a directory

```python
for entry in await files.read_directory("/workspace"):
    print(entry.name, entry.type)
```

```text
main.py file
numbers.txt file
```

Each entry is a [`SandboxDirectoryEntry`](api.md#sandboxdirectoryentry). Its
`type` is one of `"file"`, `"directory"`, `"symlink"`, `"block_device"`,
`"character_device"`, `"fifo"` or `"socket"`. A symbolic link is reported as
`"symlink"`, not as the type of its target. Entries come in the order the
filesystem returns them, not sorted.

## Get a file's size and times

```python
info = await files.stat("/workspace/main.py")
print(info.size, info.modified_at)
```

```text
12 2026-10-29 20:44:35.120000+00:00
```

[`stat()`](api.md#files) follows a symbolic link and describes its target.
`lstat()` describes the link itself. Both return a
[`SandboxFileStat`](api.md#sandboxfilestat), whose times are UTC `datetime`
values.

## Create, rename and remove

```python
await files.mkdir("/workspace/out/logs", recursive=True)
await files.rename("/workspace/main.py", "/workspace/app.py")
await files.remove("/workspace/out", recursive=True)
```

- `mkdir()` without `recursive` fails with `EEXIST` if the directory exists,
  and with `ENOENT` if its parent doesn't.
- `rename()` follows Linux `rename` rules. It replaces an existing file at the
  destination. Moving across filesystems, for example into an
  [S3 mount](s3-mounts.md), fails with `EXDEV`. The package never falls back to
  copying.
- `remove()` without `recursive` refuses a directory. `force=True` succeeds
  when the path doesn't exist, and still raises every other error.

## Use relative paths

Every path must be absolute unless you pass `cwd`:

```python
await files.write_file("notes.txt", "draft", cwd="/workspace")
```

`cwd` must be absolute. A relative path without it raises `ValueError` before
anything runs in the container.

## Run as another user

By default the shim runs as the container's default user. Pass `user`
as numeric IDs to create files owned by someone else, and to have Linux check
that user's permissions:

```python
await files.write_file("/home/app/notes.txt", "draft", user="1000:1000")
```

Names such as `"app"` raise `ValueError`. Only `"uid:gid"` is accepted.

## Handle errors

When Linux rejects an operation, the call raises
[`SandboxFileError`](api.md#errors). Its `code` is the errno name:

```python
from sandboxes import SandboxFileError

try:
    await files.read_file("/workspace/missing.txt")
except SandboxFileError as error:
    print(error.code, error.operation, error.path)
```

```text
ENOENT read_file /workspace/missing.txt
```

Common codes are `ENOENT` (missing path), `EEXIST` (already exists), `EACCES`
(no permission), `ENOTDIR` (a parent isn't a directory), `EISDIR` (the path is
a directory) and `ENOTEMPTY` (a directory isn't empty).

## Cancel an operation

Pass an `asyncio.Event` as `abort`. Setting it kills the shim and the call
raises [`SandboxAbortError`](api.md#errors):

```python
import asyncio

abort = asyncio.Event()
asyncio.get_running_loop().call_later(5, abort.set)
await files.write_file("/workspace/numbers.txt", numbers(), abort=abort)
```

An event can't be sent over RPC, so create it inside the Durable Object.
