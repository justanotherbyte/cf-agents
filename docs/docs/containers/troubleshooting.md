---
title: "Troubleshoot containers-py errors and status codes"
description: "Causes and fixes for containers-py errors: missing Wrangler config, 503 and 500 responses, ContainerProxy errors, blocked hosts and containers that never sleep."
---

Each entry starts with the text you see in a log or a response. Search this
page for the first line of your error.

## Responses from `fetch`

`fetch()` and `container_fetch()` return a `Response` instead of raising when
they can't reach the container. Check the status first:

| Status | Body starts with                                    | Cause                                                      |
| ------ | --------------------------------------------------- | ---------------------------------------------------------- |
| `503`  | `There is no Container instance available`          | No instance answered in time. See below.                   |
| `429`  | `you are requesting too many containers per second` | The account is starting containers too fast. Retry later.  |
| `500`  | `Failed to start container:`                        | The container didn't start. The rest of the body says why. |
| `500`  | `Container suddenly disconnected, try again`        | The container exited or crashed while serving the request. |
| `500`  | `Error proxying request to container:`              | The SDK couldn't forward the request.                      |

## `There is no Container instance available at this time`

`start()` and `start_and_wait_for_ports()` raise the same condition as
`RuntimeError: there is no container instance that can be provided to this
durable object`.

**Cause.** The SDK got no answer from an instance within
`instance_get_timeout_ms`, 8,000 milliseconds by default. Common reasons:

- The first deploy is still provisioning. This takes a few minutes.
- Every instance up to `max_instances` is already running.

**Fix.**

1. Run `npx wrangler containers list` and check whether the deployment is
   still in progress.
2. If it has finished, compare the number of names you route to with
   `max_instances` in `wrangler.jsonc`. With `get_random(binding, n)`, keep `n`
   at or below `max_instances`.
3. Raise `max_instances`, or lower `sleep_after` so idle instances stop
   sooner.
4. For large images, pass a longer `instance_get_timeout_ms` to
   `start_and_wait_for_ports()`.

## `RuntimeError: Containers have not been enabled for this Durable Object class`

**Cause.** The Durable Object class has no `containers` entry in your Wrangler
config, or the entry's `class_name` doesn't match the class.

**Fix.** Add an entry whose `class_name` is the exact name of your `Container`
subclass:

=== "wrangler.jsonc"

    ```jsonc
    "containers": [{ "class_name": "MyContainer", "image": "./Dockerfile" }]
    ```

=== "wrangler.toml"

    ```toml
    [[containers]]
    class_name = "MyContainer"
    image = "./Dockerfile"
    ```

=== "cloudflare.config.py"

    ```python
    from wynd.config import Container, exports

    exports={
        "MyContainer": exports.DurableObject(
            container=Container(name="my-container", dockerfile="./Dockerfile"),
        ),
    },
    ```

## `ctx.exports.ContainerProxy is undefined`

The full message is `RuntimeError: ctx.exports.ContainerProxy is undefined,
import ContainerProxy from the containers package in your worker entrypoint`.

**Cause.** The class sets a host list or an outbound handler, or a runtime
method such as `allow_host()` changed the rules, and the entry module doesn't
export `ContainerProxy`. `enable_internet = False` on its own doesn't need it.
The container doesn't start, and `fetch()` returns a `500` that starts with
`Failed to start container:`. A runtime method raises the error directly.

**Fix.** Import it in `src/entry.py`, even though you never call it:

```python title="src/entry.py"
from containers import ContainerProxy  # noqa: F401
```

If the message says `ctx.exports is undefined` instead, also update
`compatibility_date` in `wrangler.jsonc`.

## `intercept_https is enabled, but ctx.container.interceptOutboundHttps is not available`

**Cause.** `intercept_https = True` needs a `compatibility_date` of
`2026-04-02` or later.

**Fix.** Update `compatibility_date` in `wrangler.jsonc` and update Wrangler.

## `Origin is disallowed` (HTTP 520)

**Cause.** The container requested a host that a rule blocks: the host is in
`denied_hosts`, `allowed_hosts` is set and doesn't include it, or
`enable_internet` is `False` and no rule allows it.

**Fix.** Add the host to `allowed_hosts`, or add a handler for it. See
[Precedence](outbound-traffic.md#precedence) for the order the SDK checks
rules in.

## `ValueError: No port configured for this container`

**Cause.** A request reached `fetch()` or `container_fetch()` with no port, and
the class doesn't set `default_port`.

**Fix.** Set `default_port` on the class, or pick the port per request with
`container.fetch(switch_port(request, port))`.

## `Failed to verify port <port> is available after <n>ms`

This line comes from the default `on_error()` log. `fetch()` returns a `500`
that starts with `Failed to start container:`.

**Cause.** The container is running, but nothing answered on the port within
`port_ready_timeout_ms`, 20,000 milliseconds by default. The container stays
in the `running` state, and the next request waits for the port again.

**Fix.**

1. Check that your server listens on the port in `default_port` or
   `required_ports`, on `0.0.0.0`, not `127.0.0.1`.
2. If the server needs longer to boot, call `start_and_wait_for_ports()` with
   a larger `port_ready_timeout_ms` before you send requests.

## `Container exited before we could determine the container health`

**Cause.** The container process exited before the SDK could reach it. The
message ends with the exit code. If the process exits after that, while the
SDK waits for a port, the log says `Container crashed while checking for
ports` instead.

**Fix.** Run the image locally with `docker run -p 8080:8080 <image>` and read
its output. A common cause is a server that listens on `127.0.0.1` instead of
`0.0.0.0`.

## The container never goes to sleep

**Cause.** One of these:

- An override of `on_activity_expired()` returns without stopping the
  container, so the SDK renews the timer.
- A WebSocket or a streaming response is still open. Open connections count as
  activity.

**Fix.** Call `await self.stop()` at the end of `on_activity_expired()`, and
close WebSockets the client no longer uses. To confirm, log from
`on_activity_expired()` and check that the log line appears after
`sleep_after`.

## Container code changes don't show up in `pywrangler dev`

**Cause.** `dev` reloads Worker code on save but doesn't rebuild the image.

**Fix.** Press `r` in the `dev` session to rebuild it.

## Read the SDK's logs

The SDK logs to the `containers.container` logger with the standard
[`logging`](https://docs.python.org/3/library/logging.html) module. Errors are
on by default. To see routine events such as an activity timeout, lower the
level at the top of `src/entry.py`:

```python
import logging

logging.getLogger("containers").setLevel(logging.INFO)
```

The output appears in `pywrangler dev` and in your Worker's
[logs](https://developers.cloudflare.com/workers/observability/logs/).
