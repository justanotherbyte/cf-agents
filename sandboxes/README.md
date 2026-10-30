# crosswind-sandboxes

Use `crosswind-sandboxes` to work with files, S3 mounts and directory backups in a running container from a Python Worker. It ports [`@cloudflare/sandbox`](https://developers.cloudflare.com/sandbox/reference/) 1.0.3.

The package has no Python dependencies. It calls `ctx.container` through the Pyodide FFI. Inside the container, it runs the official `sandbox-shim` binary using shim protocol version 1.

## Requirements

- The container image must contain `/usr/local/bin/sandbox-shim`, copied from `cloudflare/sandbox:<VERSION>`.
- The container must be running. No class starts, stops, or monitors it.
- `nodejs_compat` is not needed. Errno names come from a fixed table of Linux values.

## Example

```python
from sandboxes import DirectoryBackup, Files
from workers import DurableObject

# Export the gateways so ctx.exports can reach them.
from sandboxes import DirectoryBackupGateway, S3Gateway  # noqa: F401


class Workspace(DurableObject):
    async def snapshot(self) -> dict:
        container = self.ctx.container
        if not container.running:
            container.start()

        files = Files(container)
        await files.write_file("/workspace/notes.txt", "hello")
        entries = await files.read_directory("/workspace")

        backups = DirectoryBackup(
            container, self.ctx.exports.DirectoryBackupGateway, binding="BACKUPS"
        )
        record = await backups.backup("/workspace")
        return {"entries": [entry.name for entry in entries], "record": record}
```

## Differences from the TypeScript SDK

- Names use `snake_case`, and option objects become keyword-only arguments.
- `AbortSignal` becomes an `asyncio.Event` passed as `abort=`. Setting it raises `SandboxAbortError`. An event can't cross RPC, so cancellation works only inside the Durable Object.
- Invalid values raise `ValueError`, and values of the wrong type raise `TypeError`.
- Errors are subclasses of `SandboxError`. Check them with `isinstance`. There is no `is()` helper.
- `stat()` returns a `SandboxFileStat` dataclass with an `int` size and UTC `datetime` times. File types use `snake_case`: `block_device` and `character_device`.
- `read_file()` returns a JS `Response`. A failure after the file opens errors the body with a JS `Error` whose `name` is the Python exception's class name.
- `DirectoryBackupRecord` is a `TypedDict` compatible with the JS SDK's records.

The [crosswind-sandboxes API reference](https://github.com/justanotherbyte/crosswind/blob/main/docs/docs/sandboxes/api.md) lists every class, method and error.
