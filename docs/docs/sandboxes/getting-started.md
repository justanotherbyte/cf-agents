---
title: "Create a Cloudflare sandbox from a Python Worker"
description: "Install crosswind-sandboxes, add sandbox-shim to a container image, write a Durable Object that writes and reads files in the sandbox, then run and deploy it."
---

This guide builds a Worker that starts a sandbox, writes a file in it and reads
the file back. By the end, `curl` against your Worker prints the file.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) and Node.js, to build and deploy
  [Python Workers](https://developers.cloudflare.com/workers/languages/python/).
- Docker, or another Docker-compatible engine, running locally. Check with
  `docker info`. Wrangler builds the image with it.
- A Cloudflare account on the Workers Paid plan, which Containers requires.

## 1. Create a Python Worker

```sh
uvx --from workers-py pywrangler init
cd hello-sandbox  # the directory you chose during init
uv add crosswind-sandboxes
```

## 2. Write the Dockerfile

Any Linux image works if it contains `sandbox-shim` and keeps running. This one
copies the shim out of Cloudflare's image into Debian:

```dockerfile title="Dockerfile"
FROM docker.io/cloudflare/sandbox:1.0.3 AS sandbox

FROM debian:bookworm-slim
COPY --from=sandbox /usr/local/bin/sandbox-shim /usr/local/bin/sandbox-shim
CMD ["sleep", "infinity"]
```

Keep the tag at `1.0.3`. Another version of the shim may speak a different
protocol, and every call then raises
[`SandboxProtocolError`](api.md#errors).

`CMD` keeps the container alive. If the main process exits, the container
stops and later calls fail.

## 3. Write the Durable Object

```python title="src/entry.py"
from sandboxes import Files
from workers import DurableObject, Request, Response, WorkerEntrypoint


class Sandbox(DurableObject):
    async def hello(self, name: str) -> str:
        container = self.ctx.container
        if not container.running:
            container.start()

        files = Files(container)
        await files.mkdir("/workspace", recursive=True)
        await files.write_file("/workspace/hello.txt", f"hello, {name}\n")

        entries = await files.read_directory("/workspace")
        response = await files.read_file("/workspace/hello.txt")
        return f"{[entry.name for entry in entries]}\n{await response.text()}"


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        sandbox = self.env.SANDBOX.getByName("demo")
        return Response(await sandbox.hello("ada"))
```

`getByName("demo")` picks the sandbox. Every request with the same name reaches
the same Durable Object and the same container, so files written by one
request are there for the next. Use a user or session ID as the name to give
each one its own sandbox.

`read_file()` returns a JavaScript `Response`. Await `.text()`, `.bytes()` or
`.json()` on it, or return it to the client as it is.

## 4. Configure Wrangler

```jsonc title="wrangler.jsonc"
{
  "name": "hello-sandbox",
  "main": "src/entry.py",
  "compatibility_date": "2026-05-12",
  "compatibility_flags": ["python_workers"],
  "containers": [
    { "class_name": "Sandbox", "image": "./Dockerfile", "max_instances": 5 }
  ],
  "durable_objects": {
    "bindings": [{ "class_name": "Sandbox", "name": "SANDBOX" }]
  },
  "migrations": [{ "tag": "v1", "new_sqlite_classes": ["Sandbox"] }]
}
```

`containers[].class_name` links the image to the `Sandbox` class.
`durable_objects.bindings` makes the class reachable as `self.env.SANDBOX`.
For every option, see
[Wrangler configuration](https://developers.cloudflare.com/containers/configuration/wrangler/).

## 5. Run it

```sh
uv run pywrangler dev
```

In another terminal:

```sh
curl http://localhost:8787
```

```text
['hello.txt']
hello, ada
```

The first request starts the container, which takes a few seconds. Later
requests reuse it.

## 6. Deploy

```sh
uv run pywrangler deploy
```

Wrangler builds the image, pushes it to Cloudflare's registry and deploys the
Worker. The first deploy takes a few minutes before containers can start.

## Next steps

- [Work with files](files.md) covers every file operation and its errors.
- [Mount an S3 bucket](s3-mounts.md) puts an R2 bucket at a path in the
  sandbox.
- [Back up a directory](backups.md) saves a directory to R2 and restores it.
