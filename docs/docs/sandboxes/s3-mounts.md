---
title: "Mount an R2 or S3 bucket in a Cloudflare sandbox"
description: "Use sandboxes.S3Mount and S3Gateway to mount an S3-compatible bucket at a path in a container, without giving the container any credentials."
---

[`S3Mount`](api.md#s3mount) mounts a bucket at a directory in the sandbox.
Programs in the container then read and write objects as ordinary files. It
works with R2 and any other S3-compatible store.

The container never sees your keys. The shim starts
[`s3fs`](https://github.com/s3fs-fuse/s3fs-fuse) inside the container, and
`s3fs` sends each storage request to [`S3Gateway`](api.md#s3gateway) in your
Worker. The gateway checks that the request stays inside the mounted bucket
and prefix, signs it with your credentials, and forwards it.

## 1. Add s3fs to the image

```dockerfile title="Dockerfile"
FROM docker.io/cloudflare/sandbox:1.0.3 AS sandbox

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends s3fs \
    && rm -rf /var/lib/apt/lists/*
COPY --from=sandbox /usr/local/bin/sandbox-shim /usr/local/bin/sandbox-shim
CMD ["sleep", "infinity"]
```

## 2. Store the credentials as secrets

For R2, create an
[API token](https://developers.cloudflare.com/r2/api/tokens/) with access to
the bucket, then store its keys:

```sh
npx wrangler secret put R2_ACCESS_KEY_ID
npx wrangler secret put R2_SECRET_ACCESS_KEY
```

For `pywrangler dev`, put the same names in `.dev.vars`.

## 3. Export the gateway

`S3Mount` reaches the gateway through `self.ctx.exports.S3Gateway`. The runtime
only fills in `ctx.exports` with classes your entry module defines or imports,
so import `S3Gateway` in `src/entry.py` even though you never call it:

```python title="src/entry.py"
from sandboxes import S3Gateway, S3Mount  # noqa: F401
```

If the import is missing, `self.ctx.exports.S3Gateway` doesn't exist and the
mount fails.

## 4. Mount the bucket

```python title="src/entry.py"
from sandboxes import Files, S3Gateway, S3Mount  # noqa: F401
from workers import DurableObject


class Sandbox(DurableObject):
    async def mount_data(self) -> list[str]:
        container = self.ctx.container
        if not container.running:
            container.start()

        mounts = S3Mount(container, self.ctx.exports.S3Gateway)
        await mounts.mount(
            "/mnt/data",
            source={
                "type": "s3",
                "endpoint": "https://<ACCOUNT_ID>.r2.cloudflarestorage.com",
                "region": "auto",
                "bucket": "my-bucket",
                "credentials": {
                    "type": "static",
                    "access_key_id": self.env.R2_ACCESS_KEY_ID,
                    "secret_access_key": self.env.R2_SECRET_ACCESS_KEY,
                },
            },
            access="read-only",
        )

        entries = await Files(container).read_directory("/mnt/data")
        return [entry.name for entry in entries]
```

Replace `<ACCOUNT_ID>` with your Cloudflare account ID and `my-bucket` with
your bucket. The method returns the object names at the top of the bucket.

`mount()` is safe to call on every request. When the same settings are already
mounted at the path, it reuses the mount. When a different mount holds the
path, it raises [`SandboxS3MountError`](api.md#errors) with `code`
`"S3_MOUNT_CONFLICT"`.

Set `access="read-write"` to let the container create, change and delete
objects. With `"read-only"`, the gateway refuses every write.

> **Warning**
>
> Call `mount()` before you register `interceptAllOutboundHttp()` on the
> container. That catch-all intercept takes every host registered after it,
> including the gateway's.

## Mount part of a bucket

The examples below reuse `mounts` from step 4, with the `source` dict from that
step in a variable.

`key_prefix` mounts only the keys under a prefix. The gateway refuses requests
for any key outside it:

```python
await mounts.mount(
    "/mnt/user",
    source=source,
    access="read-write",
    key_prefix=f"users/{user_id}",
)
```

A file at `/mnt/user/notes.txt` is the object `users/<user_id>/notes.txt`. A
trailing `/` is added to the prefix if you leave it out.

## Pass s3fs options

`s3fs_options` adds options to the `s3fs` command line. `True` adds a flag,
`False` leaves it out, and a string or number adds `name=value`:

```python
await mounts.mount(
    "/mnt/data",
    source=source,
    access="read-only",
    s3fs_options={"stat_cache_expire": 60, "enable_noobj_cache": True},
)
```

The package sets some options itself, such as `endpoint`, `url`, `ro` and
`passwd_file`. Passing one of them raises `ValueError`.

## Check a mount

```python
inspection = await mounts.inspect("/mnt/data")
```

```python
{
    "mount_path": "/mnt/data",
    "attachment": {"status": "managed", "configuration": {...}},
    "fuse": {"status": "connected"},
    "gateway": {"status": "reachable", "upstream": {"status": "usable"}},
}
```

`inspect()` changes nothing. `attachment.status` says what holds the path:

| Status           | Meaning                                                                 |
| ---------------- | ----------------------------------------------------------------------- |
| `"absent"`       | Nothing is mounted there.                                               |
| `"managed"`      | A mount made by this package. `fuse` and `gateway` say if it works.     |
| `"stale"`        | This package mounted it, but the mount no longer works. `mount()` with the same settings repairs it. |
| `"unmanaged"`    | Another filesystem, named in `filesystem_type`.                         |
| `"incompatible"` | A mount made by a different shim protocol version.                      |

When `gateway.upstream.status` is `"rejected"`, `reason` is `"credentials"`,
`"access"`, `"not-found"` or `"other"`. Check the keys and the bucket name
first.

## Unmount

```python
await mounts.unmount("/mnt/data")
```

The gateway stops accepting the mount's requests first, then the shim
unmounts. If a process in the container still uses the path, the call raises
`SandboxS3MountError` with `code` `"S3_MOUNT_BUSY"`. Requests stay refused,
so stop that process and call `unmount()` again. Unmounting a path with
nothing mounted succeeds.

## Fetch credentials per request

For short-lived credentials, pass a provider instead of static keys. Its
`fetcher` is any object with a `fetch()` method, such as a
[service binding](https://developers.cloudflare.com/workers/runtime-apis/bindings/service-bindings/):

```python
source = {
    "type": "s3",
    "endpoint": "https://s3.us-east-1.amazonaws.com",
    "region": "us-east-1",
    "bucket": "my-bucket",
    "credentials": {"type": "provider", "fetcher": self.env.CREDENTIALS},
}
```

The gateway sends `GET https://credentials.sandbox.internal/` to the fetcher
before it signs each request. The fetcher answers with JSON:

```json
{
  "accessKeyId": "AKIA...",
  "secretAccessKey": "...",
  "sessionToken": "...",
  "expiresAt": 1793318400000
}
```

`expiresAt` is milliseconds since the Unix epoch and must be in the future.
`sessionToken` is optional. The response must be 16,384 bytes or less.
