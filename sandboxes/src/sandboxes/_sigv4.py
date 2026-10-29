"""AWS Signature Version 4 for S3 requests, as ``aws4fetch`` 1.0.20 signs them."""

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, quote, urlsplit

from ._utils import decode_uri_component

__all__ = ("S3Signer",)

_UNSIGNABLE_HEADERS = frozenset(
    {
        "authorization",
        "content-type",
        "content-length",
        "user-agent",
        "presigned-expires",
        "expect",
        "x-amzn-trace-id",
        "range",
        "connection",
    }
)
_WHITESPACE = re.compile(r"\s+")


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


@dataclass(frozen=True, slots=True)
class S3Signer:
    access_key_id: str
    secret_access_key: str
    region: str
    session_token: str | None = None

    def sign(
        self, method: str, url: str, headers: dict[str, str], now: datetime
    ) -> dict[str, str]:
        """
        Return ``headers`` with the signing headers and ``authorization`` added.

        ``headers`` must have lowercase names. ``now`` must be in UTC.
        """
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        signed = {name: value for name, value in headers.items() if name != "host"}
        signed.setdefault("x-amz-content-sha256", "UNSIGNED-PAYLOAD")
        signed["x-amz-date"] = timestamp
        if self.session_token:
            signed["x-amz-security-token"] = self.session_token

        parts = urlsplit(url)
        names = sorted(
            name for name in {"host", *signed} if name not in _UNSIGNABLE_HEADERS
        )
        values = {**signed, "host": parts.netloc}
        canonical_headers = "".join(
            f"{name}:{_WHITESPACE.sub(' ', values[name])}\n" for name in names
        )
        signed_headers = ";".join(names)
        canonical_request = "\n".join(
            (
                method.upper(),
                _canonical_path(parts.path),
                _canonical_query(parts.query),
                canonical_headers,
                signed_headers,
                signed["x-amz-content-sha256"],
            )
        )

        scope = f"{timestamp[:8]}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                timestamp,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            )
        )
        signature = hmac.new(
            self._signing_key(timestamp[:8]), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        signed["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return signed

    def _signing_key(self, date: str) -> bytes:
        key = _hmac(f"AWS4{self.secret_access_key}".encode(), date)
        for part in (self.region, "s3", "aws4_request"):
            key = _hmac(key, part)
        return key


def _canonical_path(path: str) -> str:
    # S3 signs the decoded path, encoded once.
    try:
        decoded = decode_uri_component(path.replace("+", " "))
    except ValueError:
        decoded = path
    return quote(decoded, safe="/")


def _canonical_query(query: str) -> str:
    seen: set[str] = set()
    pairs = []
    for name, value in parse_qsl(query, keep_blank_values=True):
        if not name or name in seen:
            continue
        seen.add(name)
        pairs.append((quote(name, safe=""), quote(value, safe="")))
    return "&".join(f"{name}={value}" for name, value in sorted(pairs))
