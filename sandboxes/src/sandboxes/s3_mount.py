import asyncio
import json
import math
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict
from urllib.parse import urlsplit

from . import _ffi
from ._shim import (
    SHIM_PATH,
    InterceptContainer,
    ShimControl,
    ShimSession,
    abort_writer,
    parse_json_payload,
    spawn,
)
from .errors import (
    S3MountErrorCode,
    S3MountOperation,
    SandboxAbortError,
    SandboxProtocolError,
    SandboxS3MountError,
)

__all__ = (
    "ProviderCredentials",
    "S3Credentials",
    "S3Mount",
    "S3MountAccess",
    "S3MountConfiguration",
    "S3MountInspection",
    "S3Source",
    "StaticCredentials",
)

type S3MountAccess = Literal["read-only", "read-write"]


class StaticCredentials(TypedDict):
    """
    Fixed S3 keys, such as an R2 API token's access key ID and secret.

    Attributes
    ----------
    type
        Always ``"static"``.
    access_key_id
        The access key ID.
    secret_access_key
        The secret access key.
    session_token
        A session token, for temporary credentials.
    """

    type: Literal["static"]
    access_key_id: str
    secret_access_key: str
    session_token: NotRequired[str]


class ProviderCredentials(TypedDict):
    """
    Credentials that ``S3Gateway`` fetches per request.

    ``fetcher`` answers ``GET https://credentials.sandbox.internal/`` with
    ``{"accessKeyId", "secretAccessKey", "sessionToken"?, "expiresAt"}``.
    """

    type: Literal["provider"]
    fetcher: Any


type S3Credentials = StaticCredentials | ProviderCredentials


class S3Source(TypedDict):
    """
    The bucket to mount and the credentials that reach it.

    Attributes
    ----------
    type
        Always ``"s3"``.
    endpoint
        The S3 API origin, such as
        ``"https://<ACCOUNT_ID>.r2.cloudflarestorage.com"``. HTTP or HTTPS,
        with no path, query or credentials.
    region
        The region requests are signed for. R2 uses ``"auto"``.
    bucket
        The bucket name. It can't start with ``-`` or contain ``/`` or ``:``.
    credentials
        ``StaticCredentials`` or ``ProviderCredentials``.
    """

    type: Literal["s3"]
    endpoint: str
    region: str
    bucket: str
    credentials: S3Credentials


class S3SourceConfiguration(TypedDict):
    type: Literal["s3"]
    endpoint: str
    region: str
    bucket: str


class S3fsOption(TypedDict):
    name: str
    value: NotRequired[str]


class S3MountConfiguration(TypedDict):
    """The settings of a mount, without credentials, as the container records them."""

    source: S3SourceConfiguration
    key_prefix: NotRequired[str]
    access: S3MountAccess
    s3fs_options: list[S3fsOption]


class S3MountAttachment(TypedDict):
    status: Literal["absent", "unmanaged", "incompatible", "stale", "managed"]
    filesystem_type: NotRequired[str]
    configuration: NotRequired[S3MountConfiguration]


class S3MountFuse(TypedDict):
    status: Literal["connected", "disconnected", "indeterminate"]
    detail: NotRequired[str]


class S3MountUpstream(TypedDict):
    status: Literal["usable", "unavailable", "rejected"]
    reason: NotRequired[Literal["credentials", "access", "not-found", "other"]]
    detail: NotRequired[str]


class S3MountGateway(TypedDict):
    status: Literal["unreachable", "error", "reachable"]
    reason: NotRequired[Literal["credential-provider", "protocol", "internal"]]
    detail: NotRequired[str]
    upstream: NotRequired[S3MountUpstream]


class S3MountInspection(TypedDict):
    """
    The state of a mount path.

    Only ``stale`` and ``managed`` attachments include ``gateway``, and only
    ``managed`` includes ``fuse``.
    """

    mount_path: str
    attachment: S3MountAttachment
    fuse: NotRequired[S3MountFuse]
    gateway: NotRequired[S3MountGateway]


_ROUTE_READY = b"\x01"
_ROUTE_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,58}[A-Za-z0-9])?")
_RESERVED_GUEST_PATHS = (
    "/proc/self/mountinfo",
    "/run/sandbox/s3-mounts",
    "/usr/local/bin/sandbox-shim",
)
_RESERVED_S3FS_OPTIONS = frozenset(
    {
        "ahbe_conf", "allow_other", "compat_dir", "credlib", "ecs", "endpoint",
        "f", "fg", "foreground", "fsname", "host", "iam_role", "ibm_iam_auth",
        "logfile", "nomixupload", "noproxy", "passwd_file", "profile", "proxy",
        "proxy_cred_file", "public_bucket", "ro", "rw", "subtype",
        "use_path_request_style", "use_proxy", "use_session_token", "url",
    }
)  # fmt: skip
_ERROR_CODES: dict[str, S3MountErrorCode] = {
    "busy": "S3_MOUNT_BUSY",
    "conflict": "S3_MOUNT_CONFLICT",
    "failed": "S3_MOUNT_FAILED",
    "incompatible": "S3_MOUNT_INCOMPATIBLE",
}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def route_host(route_id: str) -> str:
    return f"s3-{route_id}.sandbox.internal"


def canonical_mount_path(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("mount_path must be a string")
    if not value.startswith("/") or "\0" in value:
        raise ValueError("mount_path must be an absolute path without NUL bytes")
    if value == "/":
        raise ValueError("mount_path must not be the filesystem root")
    if value.endswith("/") or any(
        segment in ("", ".", "..") for segment in value.split("/")[1:]
    ):
        raise ValueError("mount_path must be a normalized non-root path")
    return value


def _paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(f"{right}/") or right.startswith(f"{left}/")


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or "\0" in value:
        raise ValueError(f"{name} must be a non-empty string without NUL bytes")
    return value


def _canonical_endpoint(value: object) -> str:
    endpoint = _required_string(value, "source['endpoint']")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        raise ValueError("source['endpoint'] must be a valid URL") from None
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise ValueError("source['endpoint'] must use HTTP or HTTPS")
    if (
        parts.username
        or parts.password
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "source['endpoint'] must be an HTTP(S) origin without credentials, "
            "a path, query, or fragment"
        )

    host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
    if port is not None and port != _DEFAULT_PORTS[parts.scheme]:
        host = f"{host}:{port}"
    return f"{parts.scheme}://{host}/"


def _canonical_credentials(credentials: object) -> dict[str, Any]:
    if not isinstance(credentials, Mapping):
        raise TypeError("source['credentials'] must be a dict")

    match credentials.get("type"):
        case "static":
            canonical = {
                "type": "static",
                "access_key_id": _required_string(
                    credentials.get("access_key_id"), "credentials['access_key_id']"
                ),
                "secret_access_key": _required_string(
                    credentials.get("secret_access_key"),
                    "credentials['secret_access_key']",
                ),
            }
            if credentials.get("session_token") is not None:
                canonical["session_token"] = _required_string(
                    credentials["session_token"], "credentials['session_token']"
                )
            return canonical
        case "provider":
            if not callable(getattr(credentials.get("fetcher"), "fetch", None)):
                raise TypeError("provider credentials require a Fetcher")
            return {"type": "provider", "fetcher": _ffi.unwrap(credentials["fetcher"])}
        case _:
            raise ValueError('credentials[\'type\'] must be "static" or "provider"')


def _canonical_source(source: object) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        raise TypeError("source must be a dict")
    if source.get("type") != "s3":
        raise ValueError("source['type'] must be \"s3\"")

    bucket = _required_string(source.get("bucket"), "source['bucket']")
    if bucket.startswith("-") or "/" in bucket or ":" in bucket:
        raise ValueError(
            "source['bucket'] must not start with '-' or contain '/' or ':'"
        )
    return {
        "type": "s3",
        "endpoint": _canonical_endpoint(source.get("endpoint")),
        "region": _required_string(source.get("region"), "source['region']"),
        "bucket": bucket,
        "credentials": _canonical_credentials(source.get("credentials")),
    }


def _canonical_key_prefix(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TypeError("key_prefix must be a string")
    if value.startswith("/") or "\0" in value:
        raise ValueError("key_prefix must not start with '/' or contain NUL bytes")
    return value.rstrip("/") + "/"


def _canonical_s3fs_options(options: object) -> list[S3fsOption]:
    if options is None:
        return []
    if not isinstance(options, Mapping):
        raise TypeError("s3fs_options must be a dict")

    normalized: list[S3fsOption] = []
    for name, value in options.items():
        if not isinstance(name, str) or not name or any(c in name for c in "\0,="):
            raise ValueError(
                "s3fs option names must not be empty or contain NUL, ',' or '='"
            )
        if name.lower() in _RESERVED_S3FS_OPTIONS:
            raise ValueError(f's3fs option "{name}" is owned by the sandbox SDK')
        if value is False:
            continue
        if value is True:
            normalized.append({"name": name})
        elif isinstance(value, int | float) and math.isfinite(value):
            normalized.append({"name": name, "value": _js_number(value)})
        elif isinstance(value, str) and "\0" not in value and "," not in value:
            normalized.append({"name": name, "value": value})
        else:
            raise ValueError(f's3fs option "{name}" has an invalid value')
    return sorted(normalized, key=lambda option: option["name"])


def _js_number(value: float) -> str:
    """Format a number as JavaScript's ``String()`` does for common values."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


@dataclass(frozen=True, slots=True)
class _MountRequest:
    mount_path: str
    source: dict[str, Any]
    key_prefix: str | None
    access: S3MountAccess
    s3fs_options: list[S3fsOption]

    @classmethod
    def create(
        cls,
        mount_path: object,
        source: object,
        access: S3MountAccess,
        key_prefix: object,
        s3fs_options: object,
    ) -> "_MountRequest":
        path = canonical_mount_path(mount_path)
        if any(_paths_overlap(path, reserved) for reserved in _RESERVED_GUEST_PATHS):
            raise ValueError("mount_path overlaps files required by the sandbox SDK")
        if access != "read-only" and access != "read-write":
            raise ValueError('access must be "read-only" or "read-write"')
        return cls(
            mount_path=path,
            source=_canonical_source(source),
            key_prefix=_canonical_key_prefix(key_prefix),
            access=access,
            s3fs_options=_canonical_s3fs_options(s3fs_options),
        )

    def shim_request(self) -> str:
        """The JSON the shim's ``s3-mount mount`` command takes."""
        configuration: dict[str, Any] = {
            "source": {
                key: self.source[key]
                for key in ("type", "endpoint", "region", "bucket")
            },
            "access": self.access,
            "s3fsOptions": self.s3fs_options,
        }
        if self.key_prefix is not None:
            configuration["keyPrefix"] = self.key_prefix
        return json.dumps(
            {
                "protocolVersion": 1,
                "candidateRouteId": str(uuid.uuid4()),
                "mountPath": self.mount_path,
                "configuration": configuration,
            },
            separators=(",", ":"),
        )

    def gateway_props(self, route_id: str) -> dict[str, Any]:
        return {
            "protocol_version": 1,
            "mode": "active",
            "route_id": route_id,
            "source": self.source,
            "key_prefix": self.key_prefix,
            "access": self.access,
        }


class S3Mount:
    """
    Mounts an S3-compatible bucket or prefix in a running container with ``s3fs``.

    The container never sees the credentials: ``S3Gateway`` checks and signs
    each request. Start the container first. This class never starts,
    monitors, or replaces it. Call ``mount()`` before any
    ``interceptAllOutboundHttp()``.

    Parameters
    ----------
    container
        ``ctx.container``.
    gateway
        ``ctx.exports.S3Gateway``.
    """

    def __init__(
        self, container: InterceptContainer, gateway: Callable[[Any], Any]
    ) -> None:
        self._container = container
        self._gateway = gateway

    def __repr__(self) -> str:
        return f"<S3Mount container={self._container!r}>"

    async def mount(
        self,
        mount_path: str,
        *,
        source: S3Source,
        access: S3MountAccess,
        key_prefix: str | None = None,
        s3fs_options: Mapping[str, str | float | bool] | None = None,
        abort: asyncio.Event | None = None,
    ) -> None:
        """
        Mount ``source`` at ``mount_path``.

        A mount with the same settings is reused, and leftover state repaired.

        Parameters
        ----------
        mount_path
            An absolute, normalized path, such as ``"/mnt/data"``. Not ``/``,
            and no trailing ``/``.
        source
            The bucket and its credentials.
        access
            ``"read-only"`` or ``"read-write"``.
        key_prefix
            Mount only the keys under this prefix. A trailing ``/`` is added.
        s3fs_options
            Extra ``s3fs`` options. ``True`` passes a flag, ``False`` drops it,
            and a string or number passes ``name=value``. Options the package
            sets itself, such as ``endpoint`` or ``ro``, raise ``ValueError``.
        abort
            Set it to stop the mount. The call then raises
            ``SandboxAbortError``.

        Raises
        ------
        ValueError
            An argument is invalid, or ``mount_path`` overlaps a path the
            package needs.
        SandboxS3MountError
            ``S3_MOUNT_CONFLICT`` when a different mount holds the path,
            ``S3_MOUNT_BUSY``, ``S3_MOUNT_INCOMPATIBLE`` or
            ``S3_MOUNT_FAILED``.
        """
        request = _MountRequest.create(
            mount_path, source, access, key_prefix, s3fs_options
        )
        if abort is not None and abort.is_set():
            raise SandboxAbortError()

        installed: list[str] = []

        async def install_route(route_id: str) -> None:
            installed.append(route_id)
            gateway = self._fetcher(request.gateway_props(route_id))
            try:
                await self._container.interceptOutboundHttp(
                    route_host(route_id), gateway
                )
            finally:
                if abort is not None and abort.is_set():
                    await self._deny_quietly(route_id)

        try:
            await self._exchange(
                ["mount", request.shim_request()],
                "mount",
                request.mount_path,
                abort,
                handle_route=install_route,
            )
        except BaseException:
            if installed:
                await self._deny_quietly(installed[0])
            raise

    async def inspect(
        self, mount_path: str, *, abort: asyncio.Event | None = None
    ) -> S3MountInspection:
        """
        Report the state of ``mount_path`` without changing it.

        Waits for a ``mount()`` or ``unmount()`` in progress on the path first.

        Parameters
        ----------
        mount_path
            The path to inspect.
        abort
            Set it to stop the call. The call then raises
            ``SandboxAbortError``.

        Returns
        -------
        S3MountInspection
        """
        path = canonical_mount_path(mount_path)
        session = await _start(
            self._container, ["inspect", path], abort, interactive=False
        )
        control: ShimControl | None = None
        try:
            control = session.stdout_control()
            evidence = await _read_envelope(control, "inspect", path)
            inspection = _parse_inspection(evidence, path)
            await _finish(session, control)
            session.finish()
            return inspection
        except BaseException:
            session.terminate()
            if control is not None:
                control.discard()
            raise

    async def unmount(
        self, mount_path: str, *, abort: asyncio.Event | None = None
    ) -> None:
        """
        Deny further storage requests, then unmount ``mount_path``.

        Unmounting a path with nothing mounted succeeds.

        Parameters
        ----------
        mount_path
            The path to unmount.
        abort
            Set it to stop the call. The call then raises
            ``SandboxAbortError``.

        Raises
        ------
        SandboxS3MountError
            ``S3_MOUNT_BUSY`` when the path is in use. Requests stay denied.
        """
        path = canonical_mount_path(mount_path)
        await self._exchange(
            ["unmount", path],
            "unmount",
            path,
            abort,
            handle_route=self._deny,
            allow_immediate_completion=True,
        )

    async def _exchange(
        self,
        command: list[str],
        operation: S3MountOperation,
        mount_path: str,
        abort: asyncio.Event | None,
        *,
        handle_route: Callable[[str], Awaitable[None]],
        allow_immediate_completion: bool = False,
    ) -> None:
        """Get a route from the shim, handle it, then let the shim finish."""
        session = await _start(self._container, command, abort, interactive=True)
        control: ShimControl | None = None
        stdin: Any = None
        try:
            control = session.stdout_control()
            first = await _read_envelope(control, operation, mount_path)
            if first is None and allow_immediate_completion:
                await _finish(session, control)
                session.finish()
                return

            await session.wait_for(handle_route(_parse_route_selection(first)))
            if abort is not None and abort.is_set():
                raise SandboxAbortError()
            stdin = session.stdin_writer()
            await session.wait_for(stdin.write(_ffi.to_uint8array(_ROUTE_READY)))
            await session.wait_for(stdin.close())
            stdin.releaseLock()
            stdin = None

            try:
                terminal = await _read_envelope(control, operation, mount_path)
            except SandboxS3MountError:
                await _finish(session, control)
                session.finish()
                control = None
                raise
            if terminal is not None:
                raise SandboxProtocolError(
                    "sandbox-shim returned invalid S3 mount completion data"
                )
            await _finish(session, control)
            session.finish()
        except BaseException:
            session.terminate()
            if stdin is not None:
                spawn(abort_writer(stdin))
            if control is not None:
                control.discard()
            raise

    def _fetcher(self, props: dict[str, Any]) -> Any:
        return self._gateway(_ffi.to_js_object({"props": props}))

    async def _deny(self, route_id: str) -> None:
        gateway = self._fetcher(
            {"protocol_version": 1, "mode": "deny", "route_id": route_id}
        )
        await self._container.interceptOutboundHttp(route_host(route_id), gateway)

    async def _deny_quietly(self, route_id: str) -> None:
        try:
            await self._deny(route_id)
        except Exception:
            pass


def _start(
    container: InterceptContainer,
    command: list[str],
    abort: asyncio.Event | None,
    *,
    interactive: bool,
) -> Awaitable[ShimSession]:
    options = {"stdout": "pipe", "stderr": "ignore"}
    if interactive:
        options["stdin"] = "pipe"
    return ShimSession.start(
        container, [SHIM_PATH, "s3-mount", *command], options, abort
    )


async def _finish(session: ShimSession, control: ShimControl) -> None:
    await control.expect_end()
    await session.expect_exit()
    control.release_lock()


async def _read_envelope(
    control: ShimControl, operation: S3MountOperation, mount_path: str
) -> Any:
    frame = await control.read_frame()
    if frame.kind != "data":
        raise SandboxProtocolError("sandbox-shim did not return S3 mount command data")
    envelope = parse_json_payload(
        frame.payload, "sandbox-shim returned invalid S3 mount command data"
    )
    if not isinstance(envelope, dict) or envelope.get("ok") not in (True, False):
        raise SandboxProtocolError("sandbox-shim returned an invalid S3 mount result")
    if envelope["ok"]:
        if "value" not in envelope:
            raise SandboxProtocolError(
                "sandbox-shim returned an invalid S3 mount result"
            )
        return envelope["value"]

    error = envelope.get("error")
    if not (
        isinstance(error, dict)
        and isinstance(error.get("kind"), str)
        and isinstance(error.get("detail"), str)
    ):
        raise SandboxProtocolError("sandbox-shim returned an invalid S3 mount result")
    if error["kind"] == "protocol":
        raise SandboxProtocolError(error["detail"])
    code = _ERROR_CODES.get(error["kind"])
    if code is None:
        raise SandboxProtocolError(
            f'sandbox-shim returned unknown S3 mount error kind "{error["kind"]}"'
        )
    raise SandboxS3MountError(code, operation, mount_path, error["detail"])


def _parse_route_selection(value: Any) -> str:
    if (
        isinstance(value, dict)
        and value.get("kind") == "route"
        and isinstance(value.get("routeId"), str)
        and _ROUTE_ID.fullmatch(value["routeId"])
    ):
        return value["routeId"]
    raise SandboxProtocolError("sandbox-shim returned invalid S3 mount route selection")


class _InvalidEvidence(Exception):
    pass


def _check(condition: object) -> None:
    if not condition:
        raise _InvalidEvidence


def _parse_inspection(evidence: Any, mount_path: str) -> S3MountInspection:
    try:
        return _assemble_inspection(evidence, mount_path)
    except _InvalidEvidence:
        raise SandboxProtocolError(
            "sandbox-shim returned invalid S3 mount command data"
        ) from None


def _assemble_inspection(evidence: Any, mount_path: str) -> S3MountInspection:
    _check(isinstance(evidence, dict) and isinstance(evidence.get("state"), dict))
    state = evidence["state"]
    gateway = evidence.get("gateway")
    kind = state.get("kind")
    inspection: S3MountInspection = {
        "mount_path": mount_path,
        "attachment": {"status": kind},
    }

    if kind in ("absent", "unmanaged", "incompatible"):
        _check(gateway is None)
        if kind == "unmanaged":
            _check(isinstance(state.get("filesystemType"), str))
            inspection["attachment"]["filesystem_type"] = state["filesystemType"]
        if kind == "incompatible":
            _check(_is_int(state.get("protocolVersion")))
        return inspection

    _check(kind in ("stale", "managed") and gateway is not None)
    inspection["attachment"]["configuration"] = _parse_marker(
        state.get("marker"), mount_path
    )
    if kind == "managed":
        inspection["fuse"] = _parse_fuse(state.get("fuse"))
    inspection["gateway"] = _parse_gateway(gateway)
    return inspection


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _parse_marker(marker: Any, mount_path: str) -> S3MountConfiguration:
    _check(isinstance(marker, dict))
    _check(marker.get("protocolVersion") == 1 and _is_int(marker["protocolVersion"]))
    _check(_is_str(marker.get("routeId")) and _ROUTE_ID.fullmatch(marker["routeId"]))
    _check(marker.get("mountPath") == mount_path)

    configuration = marker.get("configuration")
    _check(isinstance(configuration, dict))
    source = configuration.get("source")
    _check(isinstance(source, dict) and source.get("type") == "s3")
    _check(all(_is_str(source.get(key)) for key in ("endpoint", "region", "bucket")))
    _check(configuration.get("access") in ("read-only", "read-write"))
    options = configuration.get("s3fsOptions")
    _check(isinstance(options, list))
    for option in options:
        _check(isinstance(option, dict) and _is_str(option.get("name")))
        _check(option.get("value") is None or _is_str(option["value"]))

    parsed: S3MountConfiguration = {
        "source": {
            "type": "s3",
            "endpoint": source["endpoint"],
            "region": source["region"],
            "bucket": source["bucket"],
        },
        "access": configuration["access"],
        "s3fs_options": [
            {"name": option["name"]}
            if option.get("value") is None
            else {"name": option["name"], "value": option["value"]}
            for option in options
        ],
    }
    key_prefix = configuration.get("keyPrefix")
    if key_prefix is not None:
        _check(_is_str(key_prefix) and key_prefix.endswith("/"))
        parsed["key_prefix"] = key_prefix
    return parsed


def _parse_fuse(fuse: Any) -> S3MountFuse:
    _check(isinstance(fuse, dict))
    status = fuse.get("status")
    if status in ("connected", "disconnected"):
        return {"status": status}
    _check(status == "indeterminate" and _is_str(fuse.get("detail")))
    return {"status": "indeterminate", "detail": fuse["detail"]}


def _parse_gateway(gateway: Any) -> S3MountGateway:
    _check(isinstance(gateway, dict))
    kind = gateway.get("kind")
    detail = gateway.get("detail")
    if kind == "usable":
        return {"status": "reachable", "upstream": {"status": "usable"}}

    _check(_is_str(detail))
    match kind:
        case "gatewayUnreachable":
            return {"status": "unreachable", "detail": detail}
        case "gatewayError":
            reason = gateway.get("reason")
            _check(reason in ("credential-provider", "protocol", "internal"))
            return {"status": "error", "reason": reason, "detail": detail}
        case "upstreamUnavailable":
            return {
                "status": "reachable",
                "upstream": {"status": "unavailable", "detail": detail},
            }
        case "upstreamRejected":
            reason = gateway.get("reason")
            _check(reason in ("credentials", "access", "not-found", "other"))
            return {
                "status": "reachable",
                "upstream": {"status": "rejected", "reason": reason, "detail": detail},
            }
    raise _InvalidEvidence
