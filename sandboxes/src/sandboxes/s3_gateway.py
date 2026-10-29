"""
``S3Gateway`` checks and signs the container's ``s3fs`` requests.

The container never sees the credentials. The gateway allows only the object
and list operations ``s3fs`` uses, inside the mounted bucket and key prefix.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from workers import WorkerEntrypoint

from . import _ffi
from ._sigv4 import S3Signer
from ._utils import decode_uri_component, encode_uri_component
from .s3_mount import route_host

__all__ = ("S3Gateway",)

_CREDENTIAL_PROVIDER_URL = "https://credentials.sandbox.internal/"
_MAX_CREDENTIAL_RESPONSE_BYTES = 16384
_MAX_S3_ERROR_BODY_BYTES = 65536
_MAX_INSPECTION_DETAIL_LENGTH = 1024

_LIST_V1_PARAMETERS = frozenset(
    {"delimiter", "encoding-type", "marker", "max-keys", "prefix"}
)
_LIST_V2_PARAMETERS = frozenset(
    {
        "continuation-token",
        "delimiter",
        "encoding-type",
        "fetch-owner",
        "list-type",
        "max-keys",
        "prefix",
        "start-after",
    }
)
_COPY_CONTROL_HEADERS = (
    "x-amz-copy-source-if-match",
    "x-amz-copy-source-if-modified-since",
    "x-amz-copy-source-if-none-match",
    "x-amz-copy-source-if-unmodified-since",
    "x-amz-copy-source-range",
)
_SUPPORTED_AMZ_HEADERS = frozenset(
    {
        "authorization",
        "x-amz-content-sha256",
        "x-amz-copy-source",
        "x-amz-date",
        "x-amz-metadata-directive",
        "x-amz-security-token",
        *_COPY_CONTROL_HEADERS,
    }
)
_FORWARDED_HEADERS = frozenset(
    {
        "content-length",
        "content-md5",
        "content-type",
        "if-match",
        "if-modified-since",
        "if-none-match",
        "if-unmodified-since",
        "range",
        "x-amz-copy-source",
        "x-amz-metadata-directive",
        *_COPY_CONTROL_HEADERS,
    }
)
_CREDENTIAL_ERROR_CODES = frozenset(
    {
        "AuthorizationHeaderMalformed",
        "ExpiredToken",
        "InvalidAccessKeyId",
        "InvalidToken",
        "RequestTimeTooSkewed",
        "SignatureDoesNotMatch",
        "TokenRefreshRequired",
    }
)
_PAYLOAD_HASH = re.compile(r"[a-fA-F0-9]{64}")
_DECIMAL = re.compile(r"0|[1-9][0-9]*")
_S3_ERROR_CODE = re.compile(r"<Code>([A-Za-z0-9]+)</Code>")


class S3Gateway(WorkerEntrypoint):
    """
    Signs the container's storage requests for ``S3Mount``.

    Export it from the Worker and pass ``ctx.exports.S3Gateway`` to ``S3Mount``.
    """

    async def fetch(self, request: Any) -> Any:
        props = _ffi.to_py(self.ctx.props)
        return await handle_s3_request(_ffi.unwrap(request), props)


async def handle_s3_request(request: Any, props: dict[str, Any]) -> Any:
    if props.get("protocol_version") != 1:
        return _inspection_response(
            "gateway-protocol", "gateway protocol is incompatible", 500
        )
    if props.get("mode") == "deny":
        return _inspection_response(
            "rejected-access", "mount route has been revoked", 403
        )

    headers = dict(_ffi.header_items(request.headers))
    authorization = _authorize(request.url, request.method.upper(), headers, props)
    if isinstance(authorization, str):
        return _inspection_response("rejected-access", authorization, 403)

    try:
        signer = await _resolve_signer(props["source"], request.signal)
    except Exception as error:
        if request.signal.aborted:
            raise
        detail = (
            str(error)
            if isinstance(error, _CredentialProviderError)
            else "credential provider failed"
        )
        return _inspection_response("gateway-credential-provider", detail, 503)

    signed = signer.sign(
        request.method,
        authorization.upstream_url,
        _forwarding_headers(headers),
        datetime.now(UTC),
    )
    init = {"method": request.method, "headers": signed, "signal": request.signal}
    if not _ffi.is_absent(request.body):
        init["body"] = request.body
    try:
        upstream = await _ffi.js_fetch(authorization.upstream_url, init)
    except Exception as error:
        if not authorization.inspection:
            raise
        return _inspection_response(
            "unavailable", str(error) or "upstream request failed", 503
        )

    if not authorization.inspection:
        return upstream
    return await _classify_inspection(upstream)


@dataclass(frozen=True, slots=True)
class _Authorized:
    inspection: bool
    upstream_url: str


@dataclass(frozen=True, slots=True)
class _Operation:
    kind: str
    mutation: bool
    prefix: str | None = None
    list_version: int = 0


class _Parameters:
    """Query parameters, parsed as ``URLSearchParams`` parses them."""

    def __init__(self, query: str) -> None:
        self.pairs = parse_qsl(query, keep_blank_values=True)
        self.first: dict[str, str] = {}
        for name, value in self.pairs:
            self.first.setdefault(name, value)

    def __len__(self) -> int:
        return len(self.pairs)

    def __contains__(self, name: str) -> bool:
        return name in self.first

    def get(self, name: str) -> str | None:
        return self.first.get(name)

    def has_duplicates(self) -> bool:
        return len(self.first) != len(self.pairs)

    def only(self, allowed: frozenset[str]) -> bool:
        return self.first.keys() <= allowed

    def exactly(self, *names: str) -> bool:
        return len(self.pairs) == len(names) and all(
            name in self.first for name in names
        )

    def exactly_empty(self, *names: str) -> bool:
        return self.exactly(*names) and all(self.first[name] == "" for name in names)


def _authorize(
    url: str, method: str, headers: dict[str, str], props: dict[str, Any]
) -> _Authorized | str:
    """Return where to send an allowed request, or why it isn't allowed."""
    parts = urlsplit(url)
    key_prefix: str | None = props.get("key_prefix")
    if parts.hostname != route_host(props["route_id"]):
        return "request host does not match the mount route"
    if _uses_aws_chunked_payload(headers):
        return "aws-chunked payloads are not supported"
    unsupported = next(
        (
            name
            for name in headers
            if name.startswith("x-amz-")
            and not name.startswith("x-amz-meta-")
            and name not in _SUPPORTED_AMZ_HEADERS
        ),
        None,
    )
    if unsupported is not None:
        return f"S3 header {unsupported} is not permitted for this mount"

    target = _parse_target(parts.path)
    if target is None or target[0] != props["source"]["bucket"]:
        return "request is outside the mounted bucket"
    operation = _classify(method, target[1], _Parameters(parts.query))
    if operation is None:
        return "request is not a supported s3fs operation"
    if props["access"] == "read-only" and operation.mutation:
        return "the mount is read-only"
    if operation.kind == "object" and not _object_within_prefix(target[1], key_prefix):
        return "request is outside the mounted key prefix"
    if operation.kind == "list" and not _list_within_prefix(
        operation.prefix, key_prefix
    ):
        return "list request is outside the mounted key prefix"

    rejection = _check_copy_and_metadata(method, operation, headers, props)
    if rejection is not None:
        return rejection

    inspection = (
        operation.list_version == 2
        and headers.get("user-agent") == "sandbox-shim/1"
        and _Parameters(parts.query).get("max-keys") == "1"
        and operation.prefix == key_prefix
    )
    return _Authorized(inspection, _upstream_url(parts, props["source"]["endpoint"]))


def _check_copy_and_metadata(
    method: str, operation: _Operation, headers: dict[str, str], props: dict[str, Any]
) -> str | None:
    copy_source = headers.get("x-amz-copy-source")
    if copy_source is None and any(name in headers for name in _COPY_CONTROL_HEADERS):
        return "copy controls require a copy source"
    if copy_source is not None:
        if operation.kind != "object" or method != "PUT":
            return "copy source is not valid for this operation"
        if not _is_scoped_copy_source(copy_source, props):
            return "copy source is outside the mounted bucket or key prefix"

    directive = headers.get("x-amz-metadata-directive")
    if directive is not None and (
        copy_source is None or directive not in ("COPY", "REPLACE")
    ):
        return "invalid copy metadata directive"
    has_metadata = any(name.startswith("x-amz-meta-") for name in headers)
    if has_metadata and (operation.kind != "object" or method not in ("PUT", "POST")):
        return "object metadata is not valid for this operation"
    return None


def _uses_aws_chunked_payload(headers: dict[str, str]) -> bool:
    if headers.get("x-amz-content-sha256", "").startswith("STREAMING-"):
        return True
    encodings = headers.get("content-encoding", "").split(",")
    return any(encoding.strip().lower() == "aws-chunked" for encoding in encodings)


def _parse_target(path: str) -> tuple[str, str] | None:
    """The bucket and key of a path-style URL."""
    if not path.startswith("/"):
        return None
    encoded_bucket, _, encoded_key = path[1:].partition("/")
    if not encoded_bucket:
        return None
    try:
        return decode_uri_component(encoded_bucket), decode_uri_component(encoded_key)
    except ValueError:
        return None


def _classify(method: str, key: str, parameters: _Parameters) -> _Operation | None:
    if parameters.has_duplicates():
        return None
    if key == "":
        return _classify_bucket(method, parameters)
    return _classify_object(method, parameters)


def _classify_bucket(method: str, parameters: _Parameters) -> _Operation | None:
    if method == "HEAD" and not parameters:
        return _Operation("bucket", mutation=False)
    if method != "GET":
        return None
    if parameters.exactly_empty("location"):
        return _Operation("bucket", mutation=False)

    list_type = parameters.get("list-type")
    if list_type == "2":
        allowed, version = _LIST_V2_PARAMETERS, 2
    elif list_type is None:
        allowed, version = _LIST_V1_PARAMETERS, 1
    else:
        return None
    if not parameters.only(allowed) or not _valid_list_parameters(parameters, version):
        return None
    return _Operation("list", False, parameters.get("prefix"), version)


def _valid_list_parameters(parameters: _Parameters, version: int) -> bool:
    max_keys = parameters.get("max-keys")
    if max_keys is not None and not _is_integer_in_range(max_keys, 0, 1000):
        return False
    if parameters.get("encoding-type") not in (None, "url"):
        return False
    if version == 1:
        return True

    if parameters.get("fetch-owner") not in (None, "true", "false"):
        return False
    if "continuation-token" in parameters:
        return "start-after" not in parameters and bool(
            parameters.get("continuation-token")
        )
    return True


def _classify_object(method: str, parameters: _Parameters) -> _Operation | None:
    if not parameters:
        if method in ("GET", "HEAD"):
            return _Operation("object", mutation=False)
        if method in ("PUT", "DELETE"):
            return _Operation("object", mutation=True)
        return None

    upload_id = parameters.get("uploadId")
    if method == "POST" and parameters.exactly_empty("uploads"):
        return _Operation("object", mutation=True)
    if (
        method == "PUT"
        and parameters.exactly("partNumber", "uploadId")
        and _is_integer_in_range(parameters.get("partNumber"), 1, 10000)
        and upload_id
    ):
        return _Operation("object", mutation=True)
    if method in ("POST", "DELETE") and parameters.exactly("uploadId") and upload_id:
        return _Operation("object", mutation=True)
    return None


def _is_integer_in_range(value: str | None, minimum: int, maximum: int) -> bool:
    return (
        value is not None
        and bool(_DECIMAL.fullmatch(value))
        and minimum <= int(value) <= maximum
    )


def _object_within_prefix(key: str, prefix: str | None) -> bool:
    return prefix is None or key == prefix[:-1] or key.startswith(prefix)


def _list_within_prefix(value: str | None, prefix: str | None) -> bool:
    return prefix is None or (value is not None and value.startswith(prefix))


def _is_scoped_copy_source(value: str, props: dict[str, Any]) -> bool:
    if not value or "?" in value:
        return False
    try:
        path = decode_uri_component(value)
    except ValueError:
        return False

    bucket, separator, key = path.removeprefix("/").partition("/")
    if not bucket or not separator or not key:
        return False
    return bucket == props["source"]["bucket"] and _object_within_prefix(
        key, props.get("key_prefix")
    )


def _upstream_url(parts: Any, endpoint: str) -> str:
    upstream = urlsplit(endpoint)
    return urlunsplit((upstream.scheme, upstream.netloc, parts.path, parts.query, ""))


def _forwarding_headers(incoming: dict[str, str]) -> dict[str, str]:
    """The request headers to sign and send upstream."""
    headers = {
        name: value
        for name, value in incoming.items()
        if name in _FORWARDED_HEADERS or name.startswith("x-amz-meta-")
    }
    payload_hash = incoming.get("x-amz-content-sha256", "")
    is_hash = payload_hash == "UNSIGNED-PAYLOAD" or _PAYLOAD_HASH.fullmatch(
        payload_hash
    )
    headers["x-amz-content-sha256"] = payload_hash if is_hash else "UNSIGNED-PAYLOAD"
    return headers


class _CredentialProviderError(Exception):
    pass


class _BodyTooLarge(Exception):
    pass


async def _resolve_signer(source: dict[str, Any], signal: Any) -> S3Signer:
    credentials = source["credentials"]
    if credentials["type"] == "static":
        return S3Signer(
            credentials["access_key_id"],
            credentials["secret_access_key"],
            source["region"],
            credentials.get("session_token"),
        )

    try:
        response = await credentials["fetcher"].fetch(
            _CREDENTIAL_PROVIDER_URL,
            _ffi.to_js_object(
                {"headers": {"accept": "application/json"}, "signal": signal}
            ),
        )
    except Exception:
        if signal.aborted:
            raise
        raise _CredentialProviderError("credential provider request failed") from None
    if not response.ok:
        raise _CredentialProviderError(
            f"credential provider returned HTTP {response.status}"
        )

    fields = await _read_credential_response(response.body)
    return S3Signer(
        fields["accessKeyId"],
        fields["secretAccessKey"],
        source["region"],
        fields.get("sessionToken"),
    )


async def _read_credential_response(body: Any) -> dict[str, Any]:
    try:
        data = await _read_bounded(body, _MAX_CREDENTIAL_RESPONSE_BYTES)
    except _BodyTooLarge:
        raise _CredentialProviderError(
            "credential provider response is too large"
        ) from None
    try:
        fields = json.loads((data or b"").decode())
    except ValueError:
        raise _CredentialProviderError(
            "credential provider returned invalid JSON"
        ) from None

    if (
        not _valid_credential_fields(fields)
        or fields["expiresAt"] <= time.time() * 1000
    ):
        raise _CredentialProviderError(
            "credential provider returned invalid or expired credentials"
        )
    return fields


def _valid_credential_fields(fields: object) -> bool:
    def is_secret(value: object) -> bool:
        return isinstance(value, str) and bool(value) and "\0" not in value

    if not isinstance(fields, dict):
        return False
    if not fields.keys() <= {
        "accessKeyId",
        "secretAccessKey",
        "sessionToken",
        "expiresAt",
    }:
        return False
    expires_at = fields.get("expiresAt")
    return (
        is_secret(fields.get("accessKeyId"))
        and is_secret(fields.get("secretAccessKey"))
        and ("sessionToken" not in fields or is_secret(fields["sessionToken"]))
        and isinstance(expires_at, int | float)
        and not isinstance(expires_at, bool)
        and float(expires_at).is_integer()
    )


async def _read_bounded(body: Any, limit: int) -> bytes | None:
    """Read a JS body of at most ``limit`` bytes. ``None`` if there is no body."""
    if _ffi.is_absent(body):
        return None

    reader = body.getReader()
    chunks: list[bytes] = []
    length = 0
    try:
        while not (result := await reader.read()).done:
            chunks.append(_ffi.to_bytes(result.value))
            length += len(chunks[-1])
            if length > limit:
                await reader.cancel()
                raise _BodyTooLarge
    finally:
        reader.releaseLock()
    return b"".join(chunks)


async def _classify_inspection(response: Any) -> Any:
    status = response.status
    if response.ok:
        return _inspection_response("usable", "upstream list request succeeded", status)
    if status == 401:
        return _inspection_response(
            "rejected-credentials",
            f"upstream rejected credentials with HTTP {status}",
            status,
        )
    if status == 403:
        code = await _read_s3_error_code(response.body)
        result = (
            "rejected-credentials"
            if code in _CREDENTIAL_ERROR_CODES
            else "rejected-access"
        )
        detail = "upstream denied the inspection request"
        return _inspection_response(
            result, detail if code is None else f"{detail} with {code}", status
        )
    if status == 404:
        return _inspection_response(
            "rejected-not-found", "upstream bucket was not found", status
        )
    if status == 429 or status >= 500:
        return _inspection_response(
            "unavailable", f"upstream returned HTTP {status}", status
        )
    return _inspection_response(
        "rejected-other", f"upstream rejected the request with HTTP {status}", status
    )


async def _read_s3_error_code(body: Any) -> str | None:
    try:
        data = await _read_bounded(body, _MAX_S3_ERROR_BODY_BYTES)
    except _BodyTooLarge:
        return None
    match = _S3_ERROR_CODE.search((data or b"").decode(errors="replace"))
    return match[1] if match else None


def _inspection_response(result: str, detail: str, status: int) -> Any:
    bounded = detail[:_MAX_INSPECTION_DETAIL_LENGTH]
    return _ffi.response(
        None,
        status=status,
        headers={
            "x-sandbox-s3-gateway-version": "1",
            "x-sandbox-s3-inspection-result": result,
            "x-sandbox-s3-inspection-detail": encode_uri_component(
                bounded, safe="!*'()"
            ),
        },
    )
