---
title: Outbound traffic
---

# Outbound traffic

By default a container can reach the public internet. The `Container` class
lets you restrict that, or route the container's outbound HTTP and HTTPS
requests through Python handlers that run in your Worker. Handlers run in the
Workers runtime, outside the container sandbox. They can read your Worker's
bindings and secrets, so the container never needs to hold credentials.

This page covers the Python API. For how egress works at the platform level,
including non-HTTP traffic and ports, see
[Handle outbound traffic](https://developers.cloudflare.com/containers/configuration/outbound-traffic/).

## Enable interception

Outbound handlers and host lists are served by a Worker entrypoint called
`ContainerProxy`. It must be importable from your Worker's entry module, even
if you never reference it:

```python title="src/entry.py"
from containers import Container, ContainerProxy, get_container  # noqa: F401
```

If it's missing, containers with any outbound configuration fail to start, with
`RuntimeError: ctx.exports.ContainerProxy is undefined`.

## Block internet access

Set `enable_internet = False` to deny outbound traffic by default:

```python
class Sandbox(Container):
    default_port = 8080
    enable_internet = False
```

The container can then only reach hosts you allow, or hosts an outbound handler
answers for.

## Allow and deny hosts

`allowed_hosts` is an allowlist. When it's set, requests to any host not on it
are blocked, even when `enable_internet` is `True`. `denied_hosts` is a
blocklist that always wins, over both the allowlist and any handler.

```python
class Sandbox(Container):
    default_port = 8080
    enable_internet = False
    allowed_hosts = ["pypi.org", "files.pythonhosted.org", "*.example.com"]
    denied_hosts = ["internal.example.com"]
```

Patterns are matched against the hostname. `*` matches any run of characters,
so `*.example.com`, `api.*.com` and `goo*gle` are all valid. A trailing dot on
the hostname is ignored. Blocked requests get a `520` response with the body
`Origin is disallowed`.

!!! note

    Host lists apply to HTTP traffic, and to HTTPS once
    [HTTPS interception](#https) is on. See the
    [platform docs](https://developers.cloudflare.com/containers/configuration/outbound-traffic/)
    for how other ports and protocols are treated.

## Outbound handlers

An outbound handler is an `async` function that takes the intercepted request
and returns a `Response`:

```python
from containers import OutboundHandlerContext
from workers import Request, Response


async def handler(request: Request, env, ctx: OutboundHandlerContext) -> Response: ...
```

- `request` is the request the container made.
- `env` is your Worker's environment, with all its bindings and secrets.
- `ctx.container_id` is the ID of the Durable Object that owns the container.
- `ctx.class_name` is the name of your `Container` subclass.
- `ctx.params` holds the params given to
  [`set_outbound_handler` or `set_outbound_by_host`](#change-rules-at-runtime),
  or `None`.

Handlers run in `ContainerProxy`, not in your Durable Object, so they can't use
`self`. Write them as module-level functions.

### Handle specific hosts

`outbound_by_host` maps hostnames or glob patterns to handlers. An exact match
beats a glob:

```python
async def mock_payments(request, env, ctx):
    return Response.from_json({"status": "succeeded"})


async def block(request, env, ctx):
    return Response("blocked in tests", status=403)


class Tests(Container):
    default_port = 8080
    outbound_by_host = {
        "api.stripe.com": mock_payments,
        "*.analytics.example.com": block,
    }
```

If only `outbound_by_host` is set, the container's other traffic is left alone.

### Handle everything else

`outbound` is a catch-all for any host that no more specific rule handled:

```python
from workers import fetch


async def log_and_forward(request, env, ctx):
    print(f"[{ctx.container_id}] outbound: {request.method} {request.url}")
    return await fetch(request)


class Audited(Container):
    default_port = 8080
    outbound = log_and_forward
```

### Inject credentials

Because handlers can read `env`, they can add secrets the container never sees:

```python
from workers import Request, fetch


async def github(request, env, ctx):
    headers = [
        (key, value)
        for key, value in request.headers.items()
        if key.lower() != "authorization"
    ]
    headers.append(("authorization", f"Bearer {env.GITHUB_TOKEN}"))
    return await fetch(Request(request, headers=headers))


class Agent(Container):
    default_port = 8080
    intercept_https = True
    outbound_by_host = {"api.github.com": github}
```

### Defining handlers after the class

Handlers are plain class attributes, so you can also assign them after the
class is defined. This helps when a handler needs to refer to the class:

```python
class Agent(Container):
    default_port = 8080


async def catch_all(request, env, ctx):
    return Response(f"blocked by {ctx.class_name}", status=403)


Agent.outbound = catch_all
```

Subclasses inherit outbound configuration through normal attribute lookup.

## HTTPS

Out of the box only plain HTTP is intercepted. To intercept HTTPS too, set
`intercept_https = True`:

```python
class Agent(Container):
    default_port = 8080
    intercept_https = True
```

The runtime then terminates TLS with an ephemeral certificate authority that it
writes to `/etc/cloudflare/certs/cloudflare-containers-ca.crt` when the
container starts. Your container must trust that CA before it makes requests.
For example:

=== "Node.js"

    ```python
    class Agent(Container):
        intercept_https = True
        env_vars = {
            "NODE_EXTRA_CA_CERTS": "/etc/cloudflare/certs/cloudflare-containers-ca.crt",
        }
    ```

=== "System trust store (Debian)"

    ```sh title="entrypoint.sh"
    #!/bin/sh
    cp /etc/cloudflare/certs/cloudflare-containers-ca.crt \
       /usr/local/share/ca-certificates/cloudflare-containers-ca.crt
    update-ca-certificates
    exec "$@"
    ```

The file only exists at runtime, so install it from your entrypoint, not in
your `Dockerfile`. HTTPS interception needs a `compatibility_date` of
`2026-04-02` or later. On an older runtime, the SDK raises a `RuntimeError`
before applying any interception. See
[HTTPS traffic](https://developers.cloudflare.com/containers/configuration/outbound-traffic/)
for more.

## Change rules at runtime

Rules set as class attributes apply to every instance. To change rules for one
instance while it runs, register named handlers in `outbound_handlers`, then
switch between them:

```python
async def allow_all(request, env, ctx):
    return await fetch(request)


async def tag_tenant(request, env, ctx):
    headers = [*request.headers.items(), ("x-tenant", ctx.params["tenant"])]
    return await fetch(Request(request, headers=headers))


class Workspace(Container):
    default_port = 8080
    enable_internet = False
    outbound_handlers = {"allow_all": allow_all, "tag_tenant": tag_tenant}
```

```python
container = get_container(self.env.WORKSPACE, workspace_id)

# Route one host through a named handler, with params
await container.set_outbound_by_host("api.example.com", "tag_tenant", {"tenant": "acme"})

# Replace the catch-all handler
await container.set_outbound_handler("allow_all")

# Adjust host lists
await container.allow_host("pypi.org")
await container.deny_host("evil.example")
```

| Method | Effect |
| --- | --- |
| `set_outbound_handler(name, params=None)` | Replace the catch-all `outbound` with a named handler. |
| `set_outbound_by_host(hostname, name, params=None)` | Route one host or pattern to a named handler. |
| `set_outbound_by_hosts(handlers)` | Replace all runtime host rules. Values are a handler name, or `{"method": name, "params": ...}`. |
| `remove_outbound_by_host(hostname)` | Remove a runtime host rule. Any class-level `outbound_by_host` entry applies again. |
| `set_allowed_hosts(hosts)` / `set_denied_hosts(hosts)` | Replace the allowlist or blocklist. |
| `allow_host(hostname)` / `deny_host(hostname)` | Add one host to the allowlist or blocklist. |
| `remove_allowed_host(hostname)` / `remove_denied_host(hostname)` | Remove one host from the allowlist or blocklist. |

Changes apply to a running container immediately, without a restart. They're
stored in the Durable Object, so they survive it being evicted. Naming a
handler that isn't in `outbound_handlers` raises `ValueError`. Over RPC, pass
these arguments positionally (see
[Calling container methods](routing.md#calling-container-methods-from-a-worker)).

## Precedence

Each outbound request is checked against these rules in order. The first rule
that applies decides what happens:

1. **`denied_hosts`.** A matching host is blocked.
2. **`allowed_hosts`.** If set, a host that doesn't match is blocked.
3. **Runtime host rules** from `set_outbound_by_host`.
4. **Class host rules** from `outbound_by_host`.
5. **Runtime catch-all** from `set_outbound_handler`.
6. **Class catch-all** from `outbound`.
7. **Egress.** The request goes out to the internet if the host matched
   `allowed_hosts` or `enable_internet` is `True`. Otherwise it's blocked.

!!! note "Performance"

    If your only rules are class-level `outbound_by_host` entries, just those
    hosts are routed through `ContainerProxy`. Anything else, such as a
    catch-all, a host list or any runtime change, routes all of the
    container's HTTP traffic through it. That stays in place until the container
    restarts.
