import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from test_setup import Container, Response, Stream, data, errno_frame

from sandboxes import S3Mount, SandboxProtocolError, SandboxS3MountError
from sandboxes._sigv4 import S3Signer
from sandboxes.s3_gateway import _authorize, _forwarding_headers, handle_s3_request
from sandboxes.s3_mount import _MountRequest

ROUTE = "abc-123"
HOST = f"s3-{ROUTE}.sandbox.internal"
SOURCE: Any = {
    "type": "s3",
    "endpoint": "HTTPS://Store.Example.com:443",
    "region": "auto",
    "bucket": "bucket",
    "credentials": {"type": "static", "access_key_id": "AK", "secret_access_key": "SK"},
}


def envelope(value: Any = None, *, error: tuple[str, str] | None = None) -> bytes:
    body = (
        {"ok": True, "value": value}
        if error is None
        else {
            "ok": False,
            "error": {"kind": error[0], "detail": error[1]},
        }
    )
    return data(json.dumps(body).encode())


def gateway(options: dict[str, Any]) -> dict[str, Any]:
    return options


def test_mount_request_is_canonical() -> None:
    request = _MountRequest.create(
        "/mnt/data",
        SOURCE,
        "read-only",
        "logs//",
        {"umask": "022", "zeta": 1, "alpha": True, "off": False, "ratio": 1.5},
    )

    shim = json.loads(request.shim_request())
    assert shim["mountPath"] == "/mnt/data"
    assert shim["configuration"] == {
        "source": {
            "type": "s3",
            "endpoint": "https://store.example.com/",
            "region": "auto",
            "bucket": "bucket",
        },
        "access": "read-only",
        "s3fsOptions": [
            {"name": "alpha"},
            {"name": "ratio", "value": "1.5"},
            {"name": "umask", "value": "022"},
            {"name": "zeta", "value": "1"},
        ],
        "keyPrefix": "logs/",
    }
    assert "credentials" not in shim["configuration"]["source"]


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"mount_path": "/"}, ValueError),
        ({"mount_path": "/a/../b"}, ValueError),
        ({"mount_path": "/a/"}, ValueError),
        ({"mount_path": "/run/sandbox"}, ValueError),
        ({"mount_path": 1}, TypeError),
        ({"access": "write"}, ValueError),
        ({"key_prefix": "/abs"}, ValueError),
        ({"s3fs_options": {"passwd_file": "x"}}, ValueError),
        ({"s3fs_options": {"a": "x,y"}}, ValueError),
        ({"source": {**SOURCE, "endpoint": "https://h/path"}}, ValueError),
        ({"source": {**SOURCE, "endpoint": "ftp://h"}}, ValueError),
        ({"source": {**SOURCE, "bucket": "a/b"}}, ValueError),
        ({"source": {**SOURCE, "credentials": {"type": "provider"}}}, TypeError),
    ],
)
def test_invalid_mount_requests(
    overrides: dict[str, Any], error: type[Exception]
) -> None:
    arguments: dict[str, Any] = {
        "mount_path": "/mnt",
        "source": SOURCE,
        "access": "read-write",
        "key_prefix": None,
        "s3fs_options": None,
        **overrides,
    }
    with pytest.raises(error):
        _MountRequest.create(**arguments)


async def test_mount_installs_the_route_before_acknowledging() -> None:
    container = Container()
    process = container.script(
        stdout=[envelope({"kind": "route", "routeId": ROUTE}), envelope()]
    )

    await S3Mount(container, gateway).mount("/mnt", source=SOURCE, access="read-write")

    assert container.calls[0][0][1:3] == ["s3-mount", "mount"]
    assert container.log[1:] == [
        ("intercept", HOST, "active"),
        ("write", b"\x01"),
        ("close",),
    ]
    props = container.intercepts[0][1]["props"]
    assert props["source"]["credentials"]["secret_access_key"] == "SK"
    assert process.stdin.closed


async def test_mount_conflict_denies_the_installed_route() -> None:
    container = Container()
    container.script(
        stdout=[
            envelope({"kind": "route", "routeId": ROUTE}),
            envelope(error=("conflict", "another mount")),
        ]
    )

    with pytest.raises(SandboxS3MountError) as caught:
        await S3Mount(container, gateway).mount(
            "/mnt", source=SOURCE, access="read-only"
        )

    assert caught.value.code == "S3_MOUNT_CONFLICT"
    assert str(caught.value) == "mount '/mnt': another mount"
    assert [entry for entry in container.log if entry[0] == "intercept"] == [
        ("intercept", HOST, "active"),
        ("intercept", HOST, "deny"),
    ]


async def test_unmount_of_an_absent_path_finishes_at_once() -> None:
    container = Container()
    container.script(stdout=[envelope(None)])

    await S3Mount(container, gateway).unmount("/mnt")

    assert container.intercepts == []


async def test_unmount_denies_then_unmounts() -> None:
    container = Container()
    container.script(stdout=[envelope({"kind": "route", "routeId": ROUTE}), envelope()])

    await S3Mount(container, gateway).unmount("/mnt")

    assert container.log[1:3] == [("intercept", HOST, "deny"), ("write", b"\x01")]


async def test_inspect_managed_mount() -> None:
    marker = {
        "protocolVersion": 1,
        "routeId": ROUTE,
        "mountPath": "/mnt",
        "configuration": {
            "source": {
                "type": "s3",
                "endpoint": "https://e/",
                "region": "r",
                "bucket": "b",
            },
            "keyPrefix": "p/",
            "access": "read-only",
            "s3fsOptions": [{"name": "umask", "value": "022"}],
        },
    }
    evidence = {
        "state": {"kind": "managed", "marker": marker, "fuse": {"status": "connected"}},
        "gateway": {"kind": "upstreamRejected", "reason": "access", "detail": "nope"},
    }
    container = Container()
    container.script(stdout=[envelope(evidence)])

    inspection = await S3Mount(container, gateway).inspect("/mnt")

    assert inspection == {
        "mount_path": "/mnt",
        "attachment": {
            "status": "managed",
            "configuration": {
                "source": {
                    "type": "s3",
                    "endpoint": "https://e/",
                    "region": "r",
                    "bucket": "b",
                },
                "access": "read-only",
                "s3fs_options": [{"name": "umask", "value": "022"}],
                "key_prefix": "p/",
            },
        },
        "fuse": {"status": "connected"},
        "gateway": {
            "status": "reachable",
            "upstream": {"status": "rejected", "reason": "access", "detail": "nope"},
        },
    }


@pytest.mark.parametrize(
    "evidence",
    [
        {"state": {"kind": "absent"}, "gateway": {"kind": "usable"}},
        {"state": {"kind": "stale", "marker": {}}},
        {"state": {"kind": "unknown"}},
        "not an object",
    ],
)
async def test_inspect_rejects_inconsistent_evidence(evidence: Any) -> None:
    container = Container()
    container.script(stdout=[envelope(evidence)])

    with pytest.raises(SandboxProtocolError):
        await S3Mount(container, gateway).inspect("/mnt")


async def test_mount_commands_reject_file_error_frames() -> None:
    container = Container()
    container.script(stdout=[errno_frame(2)])

    with pytest.raises(
        SandboxProtocolError, match="did not return S3 mount command data"
    ):
        await S3Mount(container, gateway).inspect("/mnt")


def test_sigv4_matches_aws4fetch() -> None:
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    signed = S3Signer("AKID", "SECRET", "auto", "tok").sign(
        "PUT",
        "https://s3.example.com/my-bucket/dir/a%20b+c(1).txt?partNumber=2&uploadId=x%2By&uploadId=dup",
        {
            "content-type": "text/plain",
            "content-length": "5",
            "x-amz-meta-owner": "me  too",
            "x-amz-content-sha256": "UNSIGNED-PAYLOAD",
            "range": "bytes=0-1",
        },
        now,
    )
    assert signed["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKID/20260102/auto/s3/aws4_request, "
        "SignedHeaders=host;x-amz-content-sha256;x-amz-date;x-amz-meta-owner;x-amz-security-token, "
        "Signature=49818bce911846a0c6193f09776c0861448593923e75d085f4314d68dbd0214f"
    )

    signed = S3Signer("AKID", "SECRET", "auto").sign(
        "GET",
        "http://localhost:9000/b?list-type=2&prefix=a/b&max-keys=1&delimiter=%2F",
        {"x-amz-content-sha256": "UNSIGNED-PAYLOAD"},
        now,
    )
    assert signed["authorization"].endswith(
        "Signature=f3b708746187bec9099ad905459cacefb728d2de3c6ea8ebd20765fabf0743d4"
    )


PROPS: Any = {
    "protocol_version": 1,
    "mode": "active",
    "route_id": ROUTE,
    "source": {**SOURCE, "endpoint": "https://store.example.com/"},
    "key_prefix": "p/",
    "access": "read-only",
}


@pytest.mark.parametrize(
    ("method", "path", "headers", "rejection"),
    [
        ("GET", "/bucket/p/a.txt", {}, None),
        ("GET", "/bucket/p", {}, None),
        ("HEAD", "/bucket", {}, None),
        ("GET", "/bucket?list-type=2&prefix=p/x", {}, None),
        ("GET", "/bucket?location", {}, None),
        ("GET", "/other/p/a", {}, "outside the mounted bucket"),
        ("GET", "/bucket/q/a", {}, "outside the mounted key prefix"),
        ("GET", "/bucket?list-type=2&prefix=q", {}, "list request is outside"),
        ("GET", "/bucket?list-type=2", {}, "list request is outside"),
        ("PUT", "/bucket/p/a", {}, "read-only"),
        ("GET", "/bucket/p/a?acl", {}, "not a supported s3fs operation"),
        ("GET", "/bucket?prefix=p/&prefix=p/", {}, "not a supported s3fs operation"),
        ("GET", "/bucket/p/a", {"x-amz-acl": "public"}, "x-amz-acl is not permitted"),
        ("GET", "/bucket/p/a", {"content-encoding": "aws-chunked"}, "aws-chunked"),
        (
            "GET",
            "/bucket/p/a",
            {"x-amz-copy-source-range": "x"},
            "require a copy source",
        ),
        ("GET", "/bucket/p/a", {"x-amz-meta-a": "1"}, "metadata is not valid"),
    ],
)
def test_gateway_policy(
    method: str, path: str, headers: dict[str, str], rejection: str | None
) -> None:
    result = _authorize(f"http://{HOST}{path}", method, headers, PROPS)

    if rejection is None:
        assert not isinstance(result, str), result
        assert result.upstream_url == f"https://store.example.com{path}"
    else:
        assert isinstance(result, str) and rejection in result


def test_gateway_policy_copy_and_writes() -> None:
    props = {**PROPS, "access": "read-write"}
    url = f"http://{HOST}/bucket/p/b"

    assert not isinstance(
        _authorize(url, "PUT", {"x-amz-copy-source": "/bucket/p/a"}, props), str
    )
    assert "outside" in _authorize(
        url, "PUT", {"x-amz-copy-source": "/bucket/q/a"}, props
    )  # ty: ignore[unsupported-operator]
    assert not isinstance(_authorize(f"{url}?uploads", "POST", {}, props), str)
    assert not isinstance(
        _authorize(f"{url}?partNumber=1&uploadId=u", "PUT", {}, props), str
    )
    assert isinstance(
        _authorize(f"{url}?partNumber=0&uploadId=u", "PUT", {}, props), str
    )
    assert isinstance(
        _authorize(f"http://evil{url[7 + len(HOST) :]}", "GET", {}, props), str
    )


def test_inspection_requests_are_recognised() -> None:
    url = f"http://{HOST}/bucket?list-type=2&prefix=p/&max-keys=1"

    result = _authorize(url, "GET", {"user-agent": "sandbox-shim/1"}, PROPS)
    assert not isinstance(result, str) and result.inspection
    result = _authorize(url, "GET", {}, PROPS)
    assert not isinstance(result, str) and not result.inspection


def test_forwarding_drops_the_container_signature() -> None:
    headers = _forwarding_headers(
        {
            "authorization": "AWS4 forged",
            "x-amz-date": "x",
            "x-amz-content-sha256": "not-a-hash",
            "content-type": "text/plain",
            "x-amz-meta-a": "1",
            "user-agent": "s3fs",
        }
    )
    assert headers == {
        "content-type": "text/plain",
        "x-amz-meta-a": "1",
        "x-amz-content-sha256": "UNSIGNED-PAYLOAD",
    }


def js_request(method: str, url: str, headers: dict[str, str] | None = None) -> Any:
    return SimpleNamespace(
        method=method,
        url=url,
        headers=list((headers or {}).items()),
        body=None,
        signal=SimpleNamespace(aborted=False),
    )


async def test_gateway_denies_revoked_routes_and_bad_requests() -> None:
    revoked = await handle_s3_request(
        js_request("GET", f"http://{HOST}/bucket/p/a"), {**PROPS, "mode": "deny"}
    )
    outside = await handle_s3_request(
        js_request("GET", f"http://{HOST}/bucket/q/a"), PROPS
    )

    assert revoked.status == 403
    assert (
        revoked.headers["x-sandbox-s3-inspection-detail"]
        == "mount%20route%20has%20been%20revoked"
    )
    assert outside.status == 403


async def test_gateway_signs_and_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    async def fetch(url: str, init: dict[str, Any]) -> Response:
        sent.append((url, init))
        return Response(None, {"status": 200})

    monkeypatch.setattr("sandboxes._ffi.js_fetch", fetch)
    request = js_request(
        "GET", f"http://{HOST}/bucket/p/a", {"authorization": "forged"}
    )

    response = await handle_s3_request(request, PROPS)

    assert response.status == 200
    url, init = sent[0]
    assert url == "https://store.example.com/bucket/p/a"
    assert init["headers"]["authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=AK/"
    )
    assert "body" not in init


async def test_gateway_inspection_classifies_upstream_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch(url: str, init: dict[str, Any]) -> Response:
        body = Stream([b"<Error><Code>InvalidAccessKeyId</Code></Error>"])
        return Response(body, {"status": 403})

    monkeypatch.setattr("sandboxes._ffi.js_fetch", fetch)
    request = js_request(
        "GET",
        f"http://{HOST}/bucket?list-type=2&prefix=p/&max-keys=1",
        {"user-agent": "sandbox-shim/1"},
    )

    response = await handle_s3_request(request, PROPS)

    assert response.status == 403
    assert response.headers["x-sandbox-s3-inspection-result"] == "rejected-credentials"


async def test_gateway_uses_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[Any] = []

    async def fetch(url: str, init: dict[str, Any]) -> Response:
        sent.append(init)
        return Response(None, {"status": 200})

    class Provider:
        def __init__(self, fields: dict[str, Any]) -> None:
            self.fields = fields

        async def fetch(self, url: str, init: Any) -> Response:
            return Response(Stream([json.dumps(self.fields).encode()]), {"status": 200})

    monkeypatch.setattr("sandboxes._ffi.js_fetch", fetch)
    future = int(time.time() * 1000) + 60_000
    fields = {
        "accessKeyId": "TMP",
        "secretAccessKey": "S",
        "sessionToken": "T",
        "expiresAt": future,
    }
    props = {
        **PROPS,
        "source": {
            **PROPS["source"],
            "credentials": {"type": "provider", "fetcher": Provider(fields)},
        },
    }

    await handle_s3_request(js_request("GET", f"http://{HOST}/bucket/p/a"), props)
    assert sent[0]["headers"]["x-amz-security-token"] == "T"

    props["source"]["credentials"]["fetcher"] = Provider({**fields, "expiresAt": 1})
    response = await handle_s3_request(
        js_request("GET", f"http://{HOST}/bucket/p/a"), props
    )
    assert response.status == 503
    assert (
        response.headers["x-sandbox-s3-inspection-result"]
        == "gateway-credential-provider"
    )


def test_provider_fetcher_is_unwrapped_for_rpc() -> None:
    js_fetcher = SimpleNamespace(fetch=lambda *args: None)

    class FetcherWrapper:
        """Like the runtime's: the JS object is ``_binding``, other names forward to JS."""

        _binding = js_fetcher

        def __getattr__(self, name: str) -> Any:
            return lambda *args: None

    source = {
        **SOURCE,
        "credentials": {"type": "provider", "fetcher": FetcherWrapper()},
    }

    request = _MountRequest.create("/mnt", source, "read-only", None, None)

    assert (
        request.gateway_props(ROUTE)["source"]["credentials"]["fetcher"] is js_fetcher
    )
