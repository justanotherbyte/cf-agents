---
title: "Cloudflare Sandbox SDK for Python Workers"
description: "What a sandbox is, how crosswind-sandboxes reads and writes files, mounts S3 buckets and backs up directories in a container from a Python Worker."
---

Use a sandbox to run code you don't trust, such as a script an LLM wrote, a
user's build or a tool that needs a real shell. A sandbox is a Linux container
that your Worker starts and controls. Its code runs in its own filesystem and
process tree, apart from your Worker and other sandboxes.

`crosswind-sandboxes` lets a
[Python Worker](https://developers.cloudflare.com/workers/languages/python/)
work with the files in a sandbox.

```python
from sandboxes import Files
from workers import DurableObject


class Sandbox(DurableObject):
    async def greet(self) -> str:
        if not self.ctx.container.running:
            self.ctx.container.start()

        files = Files(self.ctx.container)
        await files.write_file("/tmp/hello.txt", "hello")
        return await (await files.read_file("/tmp/hello.txt")).text()
```

It is a Python port of
[`@cloudflare/sandbox`](https://developers.cloudflare.com/sandbox/) 1.0.3.
Backup records match the TypeScript SDK's, so either SDK can restore a backup
the other made.

## How it works

Each sandbox is a [Cloudflare Container](https://developers.cloudflare.com/containers/)
that belongs to one
[Durable Object](https://developers.cloudflare.com/durable-objects/). The
Durable Object reaches it as `self.ctx.container`, which the Workers runtime
provides. That object starts the container and runs commands in it.

This package adds three classes on top of `ctx.container`:

| Class                                                  | What it does                                                          |
| ------------------------------------------------------ | --------------------------------------------------------------------- |
| [`Files`](api.md#files)                                | Reads, writes, lists, renames and removes files.                      |
| [`S3Mount`](api.md#s3mount)                            | Mounts an S3 or R2 bucket as a directory in the container.            |
| [`DirectoryBackup`](api.md#directorybackup)            | Saves a directory to an R2 bucket and restores it later.              |

Each call runs `/usr/local/bin/sandbox-shim` in the container. The shim is a
small binary from Cloudflare's `cloudflare/sandbox` image. It does one file,
mount or backup operation, sends the result back and exits. Your image has to
include it. [Getting started](getting-started.md#2-write-the-dockerfile)
shows the one `COPY` line that adds it.

A request goes from the client to your Worker, then to the Durable Object that
owns the sandbox. The Durable Object calls `exec()` on `self.ctx.container` to
run `sandbox-shim`, and the shim works on the container's files.

S3 mounts and backups need credentials or bucket access. The container never
gets them. Its storage requests go out to a gateway class in your Worker,
[`S3Gateway`](api.md#s3gateway) or
[`DirectoryBackupGateway`](api.md#directorybackupgateway), which checks each
request and signs it or sends it to R2.

## What the package doesn't do

- It never starts, stops or restarts the container. Call
  `self.ctx.container.start()` before the first operation.
- It doesn't run commands. Use `self.ctx.container.exec()` from the runtime.
- It doesn't route HTTP requests to a server in the container. For that, see
  [Containers](../containers/overview.md).

## Install

Add the package to a Python Worker project:

```sh
uv add crosswind-sandboxes
```

It needs Python 3.12 or later. The sandbox container also needs Docker locally
and the Workers Paid plan. [Getting started](getting-started.md) covers the
project, the Dockerfile and the Wrangler config.

## Status

These pages cover `crosswind-sandboxes` 0.1.0, which needs Python 3.12 or
later. It speaks version 1 of the shim protocol and was tested against the
`cloudflare/sandbox:1.0.3` image. The package is pre-1.0, and its API may
change between minor releases. It is an independent community project, not an
official Cloudflare SDK.

## Next steps

- [Getting started](getting-started.md) builds a sandbox and writes a file
  in it.
- [API reference](api.md) lists every class, method and error.
