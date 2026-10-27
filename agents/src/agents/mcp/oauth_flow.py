"""The OAuth 2.1 client flow for one server, split across requests.

The SDK runs the redirect and the callback in one coroutine
(``client/auth/oauth2.py``). A Durable Object can't wait in memory for a
callback that arrives in a later request, possibly in a new isolate, so a
401 ends with `MCPAuthorizationRequiredError` and everything the callback
needs is stored (``.design/mcp_engine.md`` §7). The steps and their checks
are the SDK's (``oauth2.py``, ``auth/utils.py``, ``shared/auth_utils.py``).
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import string
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

from .errors import MCPAuthorizationRequiredError, MCPOAuthError
from .http import HttpRequest, HttpTransport, read_body, send_within_origin
from .oauth import MCPOAuthProvider, OAuthDiscoveryState
from .session import HANDSHAKE_VERSIONS, MODERN_VERSIONS
from .types import OAuthClientInformation, OAuthTokens

__all__ = ("OAuthFlow",)

_log = logging.getLogger(__name__)

_VERSIONS = (*HANDSHAKE_VERSIONS, *MODERN_VERSIONS)
_RESOURCE_PARAM_SINCE = "2025-06-18"
_VERIFIER_ALPHABET = string.ascii_letters + string.digits + "-._~"
_USABLE_AUTH_METHODS = (None, "none", "client_secret_post", "client_secret_basic")
_SECRET_AUTH_METHODS = ("client_secret_post", "client_secret_basic")
_FORM = {"content-type": "application/x-www-form-urlencoded"}


@dataclass(slots=True, kw_only=True)
class _Discovery:
    """What the protected resource and authorization server metadata say."""

    issuer: str
    auth_server_url: str | None = None
    resource_metadata_url: str | None = None
    resource_metadata: dict[str, Any] | None = None
    server_metadata: dict[str, Any] | None = None

    def endpoint(self, name: str, fallback: str, server_url: str) -> str:
        """Return the metadata's ``<name>_endpoint``, or ``<origin>/<fallback>``."""
        value = (self.server_metadata or {}).get(f"{name}_endpoint")
        return (
            value if isinstance(value, str) else urljoin(_origin(server_url), fallback)
        )

    def to_state(self, resource: str | None) -> OAuthDiscoveryState:
        state: OAuthDiscoveryState = {"authorizationServerUrl": self.issuer}
        if self.resource_metadata_url is not None:
            state["resourceMetadataUrl"] = self.resource_metadata_url
        if self.resource_metadata is not None:
            state["resourceMetadata"] = self.resource_metadata
        if self.server_metadata is not None:
            state["authorizationServerMetadata"] = self.server_metadata
        if resource is not None:
            state["resource"] = resource
        return state

    @classmethod
    def from_state(cls, state: OAuthDiscoveryState) -> "_Discovery":
        return cls(
            issuer=state.get("authorizationServerUrl", ""),
            resource_metadata_url=state.get("resourceMetadataUrl"),
            resource_metadata=state.get("resourceMetadata"),
            server_metadata=state.get("authorizationServerMetadata"),
        )


class OAuthFlow:
    """OAuth for one server: tokens on requests, a 401's next step, the exchange.

    Parameters
    ----------
    provider
        Where the server's OAuth state is stored.
    server_url
        The MCP server's endpoint.
    http
        The transport for metadata, registration and token requests.
    """

    def __init__(
        self, provider: MCPOAuthProvider, *, server_url: str, http: HttpTransport
    ) -> None:
        self.provider = provider
        self._server_url = server_url
        self._http = http
        self._tokens: OAuthTokens | None = None
        self._tokens_loaded = False
        # Concurrent 401s (discovery's parallel lists) share one refresh:
        # refresh tokens are single-use.
        self._lock = asyncio.Lock()

    # On every request

    async def authorization(self) -> str | None:
        """Return ``Bearer <access token>``, if there are tokens."""
        if not self._tokens_loaded:
            self._tokens = await self.provider.tokens()
            self._tokens_loaded = True
        if not self._tokens:
            return None
        return f"Bearer {self._tokens['access_token']}"

    async def _save_tokens(self, tokens: OAuthTokens) -> None:
        await self.provider.save_tokens(tokens)
        self._tokens, self._tokens_loaded = tokens, True

    async def _clear_tokens(self) -> None:
        await self.provider.clear_tokens()
        self._tokens, self._tokens_loaded = None, True

    # Request A: a 401

    async def on_unauthorized(
        self,
        headers: Mapping[str, str],
        *,
        protocol_version: str | None,
        allow_refresh: bool,
    ) -> None:
        """Refresh the tokens and return, or start authorizing and raise.

        Raises
        ------
        MCPAuthorizationRequiredError
            With the URL the user must visit.
        MCPOAuthError
            If discovery or registration fails.
        MCPTransportError
            If a metadata, registration or token request can't be sent.
        """
        used = self._tokens
        async with self._lock:
            if self._tokens is not None and self._tokens is not used:
                return  # another request refreshed them meanwhile: retry with those
            await self._recover(headers, protocol_version, allow_refresh=allow_refresh)

    async def _recover(
        self,
        headers: Mapping[str, str],
        protocol_version: str | None,
        *,
        allow_refresh: bool,
    ) -> None:
        challenge = headers.get("www-authenticate", "")
        discovery = await self._discover(
            _www_auth_field(challenge, "resource_metadata")
        )
        resource = self._resource(discovery, protocol_version)
        if allow_refresh and await self._refresh(discovery, resource):
            return

        info = await self._client(discovery)
        scope = _select_scope(_www_auth_field(challenge, "scope"), discovery)
        if info is None:
            info = await self._register(discovery, scope)
        auth_url = await self._authorization_url(discovery, info, scope, resource)
        await self.provider.save_discovery_state(discovery.to_state(resource))
        raise MCPAuthorizationRequiredError(auth_url)

    async def _discover(self, resource_metadata_url: str | None) -> _Discovery:
        # Step 1: protected resource metadata (RFC 9728, SEP-985 fallbacks).
        discovery = _Discovery(issuer=_origin(self._server_url))
        failed_status: int | None = None
        for url in _resource_metadata_urls(resource_metadata_url, self._server_url):
            status, metadata = await self._get_json(url)
            if status >= 500 or status == 429:
                failed_status = status
            if status != 200 or metadata is None:
                continue
            servers = metadata.get("authorization_servers")
            if not isinstance(servers, list) or not servers:
                continue
            self._check_resource(metadata)
            discovery.resource_metadata_url = url
            discovery.resource_metadata = metadata
            discovery.auth_server_url = discovery.issuer = str(servers[0])
            break
        else:
            if failed_status is not None:
                raise MCPOAuthError(
                    f"Protected resource metadata request failed: HTTP {failed_status}"
                )

        # Step 2: authorization server metadata (RFC 8414, then OIDC).
        for url in _server_metadata_urls(discovery.auth_server_url, self._server_url):
            status, metadata = await self._get_json(url)
            if status >= 500:
                break
            if status != 200 or metadata is None:
                continue
            issuer = str(metadata.get("issuer", ""))
            if not _issuers_match(issuer, discovery.issuer):
                raise MCPOAuthError(
                    "Authorization server metadata issuer mismatch: "
                    f"{issuer} != {discovery.issuer}"
                )
            if discovery.auth_server_url is None:
                discovery.issuer = issuer  # legacy path: as the server spells it
            discovery.server_metadata = metadata
            break
        return discovery

    def _check_resource(self, metadata: Mapping[str, Any]) -> None:
        resource = metadata.get("resource")
        expected = _canonical(self._server_url)
        if resource and not _resource_allowed(expected, str(resource)):
            raise MCPOAuthError(
                f"Protected resource {resource} doesn't match {expected}"
            )

    def _resource(
        self, discovery: _Discovery, protocol_version: str | None
    ) -> str | None:
        # RFC 8707: sent when there's resource metadata, or from 2025-06-18 on.
        if discovery.resource_metadata is None and not _at_least(
            protocol_version, _RESOURCE_PARAM_SINCE
        ):
            return None
        resource = _canonical(self._server_url)
        advertised = (discovery.resource_metadata or {}).get("resource")
        if advertised and _resource_allowed(resource, str(advertised)):
            return str(advertised)
        return resource

    async def _refresh(self, discovery: _Discovery, resource: str | None) -> bool:
        tokens = await self.provider.tokens()
        info = await self.provider.client_information()
        if not tokens or "refresh_token" not in tokens or info is None:
            return False
        data = {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": info["client_id"],
        }
        if resource is not None:
            data["resource"] = resource
        status, body = await self._post_form(
            discovery.endpoint("token", "/token", self._server_url), data, info
        )
        refreshed = _json_object(body) if status == 200 else None
        if refreshed is None or "access_token" not in refreshed:
            _log.warning(
                "Token refresh for %s failed: HTTP %s", self._server_url, status
            )
            await self._clear_tokens()
            return False
        # RFC 6749 §6: an omitted scope or refresh token carries forward.
        for key in ("scope", "refresh_token"):
            if key not in refreshed and key in tokens:
                refreshed[key] = tokens[key]
        await self._save_tokens(_tokens(refreshed))
        return True

    async def _client(self, discovery: _Discovery) -> OAuthClientInformation | None:
        # SEP-2352: credentials bound to another issuer are dropped, with their tokens.
        info = await self.provider.client_information()
        if info is None:
            return None
        bound = info.get("issuer")
        if bound is not None and not _issuers_match(bound, discovery.issuer):
            await self.provider.clear_client_information()
            self._tokens, self._tokens_loaded = None, True
            return None
        return info

    async def _register(
        self, discovery: _Discovery, scope: str | None
    ) -> OAuthClientInformation:
        # Step 4: dynamic client registration (RFC 7591).
        url = discovery.endpoint("registration", "/register", self._server_url)
        metadata = dict(self.provider.client_metadata)
        if scope is not None:
            metadata["scope"] = scope
        request = HttpRequest(
            method="POST",
            url=url,
            headers={"content-type": "application/json", "accept": "application/json"},
            body=json.dumps(metadata),
        )
        async with send_within_origin(self._http, request) as response:
            status, body = response.status, await read_body(response)
        registered = _json_object(body) if status in (200, 201) else None
        if registered is None or not isinstance(registered.get("client_id"), str):
            raise MCPOAuthError(f"Registration failed ({status}): {body}")
        registered.pop("issuer", None)  # the client's own stamp, never the server's
        _check_registration_usable(registered)

        # Bind the credentials to the issuer only if registration reached it.
        if discovery.server_metadata is not None and (
            "registration_endpoint" in discovery.server_metadata
            or _origin(discovery.issuer) == _origin(self._server_url)
        ):
            registered["issuer"] = discovery.issuer
        info: OAuthClientInformation = {
            **registered,
            "client_id": registered["client_id"],
        }
        await self.provider.save_client_information(info)
        return info

    async def _authorization_url(
        self,
        discovery: _Discovery,
        info: OAuthClientInformation,
        scope: str | None,
        resource: str | None,
    ) -> str:
        # Step 5: PKCE S256 and the authorization URL (oauth2.py:385).
        verifier = "".join(secrets.choice(_VERIFIER_ALPHABET) for _ in range(128))
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        state = await self.provider.create_state()
        await self.provider.save_code_verifier(state, verifier)

        params = {
            "response_type": "code",
            "client_id": info["client_id"],
            "redirect_uri": self.provider.redirect_url,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if resource is not None:
            params["resource"] = resource
        if scope is not None:
            params["scope"] = scope
            if "offline_access" in scope.split():
                params["prompt"] = "consent"  # OIDC needs it for offline_access
        endpoint = discovery.endpoint("authorization", "/authorize", self._server_url)
        return f"{endpoint}?{urlencode(params)}"

    # Request B: the callback

    async def exchange_code(self, *, code: str, state: str, iss: str | None) -> None:
        """Trade the authorization code for tokens and store them.

        The PKCE verifier is deleted whether or not the exchange succeeds.

        Raises
        ------
        MCPOAuthError
            If ``iss`` doesn't match the authorization server, the verifier or
            client is missing, or the token endpoint refuses.
        MCPTransportError
            If the token request can't be sent.
        """
        try:
            stored = await self.provider.discovery_state()
            discovery = (
                _Discovery.from_state(stored)
                if stored is not None
                else await self._discover(None)
            )
            _check_iss(iss, discovery.server_metadata)
            verifier = await self.provider.code_verifier(state)
            info = await self.provider.client_information()
            if verifier is None or info is None:
                raise MCPOAuthError("No pending authorization for this callback")

            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.provider.redirect_url,
                "client_id": info["client_id"],
                "code_verifier": verifier,
            }
            if stored is not None and "resource" in stored:
                data["resource"] = stored["resource"]
            status, body = await self._post_form(
                discovery.endpoint("token", "/token", self._server_url), data, info
            )
            tokens = _json_object(body) if status in (200, 201) else None
            if tokens is None or "access_token" not in tokens:
                raise MCPOAuthError(f"Token exchange failed ({status}): {body}")
            await self._save_tokens(_tokens(tokens))
        finally:
            await self.provider.delete_code_verifier(state)

    # HTTP

    async def _get_json(self, url: str) -> tuple[int, dict[str, Any] | None]:
        request = HttpRequest(
            method="GET",
            url=url,
            headers={
                "mcp-protocol-version": _VERSIONS[-1],
                "accept": "application/json",
            },
        )
        async with send_within_origin(self._http, request) as response:
            status = response.status
            body = await read_body(response) if status == 200 else ""
        return status, _json_object(body)

    async def _post_form(
        self, url: str, data: dict[str, str], info: OAuthClientInformation
    ) -> tuple[int, str]:
        headers = dict(_FORM)
        method = info.get("token_endpoint_auth_method")
        secret = info.get("client_secret")
        if method == "client_secret_basic" and secret:
            # RFC 6749 §2.3.1: both parts URL-encoded before base64.
            credentials = (
                f"{quote(info['client_id'], safe='')}:{quote(secret, safe='')}"
            )
            headers["authorization"] = (
                f"Basic {base64.b64encode(credentials.encode()).decode()}"
            )
        elif method == "client_secret_post" and secret:
            data = {**data, "client_secret": secret}
        request = HttpRequest(
            method="POST", url=url, headers=headers, body=urlencode(data)
        )
        async with send_within_origin(self._http, request) as response:
            return response.status, await read_body(response)


# Helpers on plain values


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _canonical(url: str) -> str:
    # RFC 8707: lowercase scheme and host, no fragment.
    parts = urlsplit(url)
    return urlunsplit(
        parts._replace(
            scheme=parts.scheme.lower(), netloc=parts.netloc.lower(), fragment=""
        )
    )


def _resource_allowed(requested: str, configured: str) -> bool:
    # A token for a parent resource is good for its children (auth_utils.py:31).
    a, b = urlsplit(requested), urlsplit(configured)
    if (a.scheme.lower(), a.netloc.lower()) != (b.scheme.lower(), b.netloc.lower()):
        return False
    return (a.path.rstrip("/") + "/").startswith(b.path.rstrip("/") + "/")


def _issuers_match(a: str, b: str) -> bool:
    # Exact, except a root issuer with and without its trailing slash (utils.py:361).
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return longer == f"{shorter}/" and shorter == _origin(shorter)


def _at_least(version: str | None, minimum: str) -> bool:
    if version not in _VERSIONS:
        return False
    return _VERSIONS.index(version) >= _VERSIONS.index(minimum)


def _www_auth_field(header: str, name: str) -> str | None:
    match = re.search(rf'{name}=(?:"([^"]+)"|([^\s,]+))', header)
    return (match.group(1) or match.group(2)) if match else None


def _resource_metadata_urls(header_url: str | None, server_url: str) -> list[str]:
    origin, path = _origin(server_url), urlsplit(server_url).path
    urls = [header_url] if header_url else []
    if path and path != "/":
        urls.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    urls.append(f"{origin}/.well-known/oauth-protected-resource")
    return urls


def _server_metadata_urls(auth_server_url: str | None, server_url: str) -> list[str]:
    if auth_server_url is None:  # the 2025-03-26 legacy path
        return [f"{_origin(server_url)}/.well-known/oauth-authorization-server"]
    origin, path = _origin(auth_server_url), urlsplit(auth_server_url).path.rstrip("/")
    if path:
        return [
            f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{origin}{path}/.well-known/openid-configuration",
        ]
    return [
        f"{origin}/.well-known/oauth-authorization-server",
        f"{origin}/.well-known/openid-configuration",
    ]


def _select_scope(challenged: str | None, discovery: _Discovery) -> str | None:
    # The challenge's scope, else the resource's, else the server's (utils.py:101),
    # plus offline_access when the server offers it, for refresh tokens.
    server = discovery.server_metadata or {}
    scope = challenged
    for metadata in (discovery.resource_metadata or {}, server):
        supported = metadata.get("scopes_supported")
        if scope is None and isinstance(supported, list):
            scope = " ".join(supported)
    offered = server.get("scopes_supported") or []
    if (
        scope is not None
        and "offline_access" in offered
        and "offline_access" not in scope.split()
    ):
        scope = f"{scope} offline_access"
    return scope


def _check_iss(iss: str | None, server_metadata: Mapping[str, Any] | None) -> None:
    # RFC 9207: simple string comparison; absent only if not advertised.
    expected = (server_metadata or {}).get("issuer")
    if iss is not None:
        if iss != expected:
            raise MCPOAuthError(
                f"Authorization response iss mismatch: {iss} != {expected}"
            )
        return
    if (server_metadata or {}).get("authorization_response_iss_parameter_supported"):
        raise MCPOAuthError("Authorization response is missing its iss parameter")


def _check_registration_usable(info: Mapping[str, Any]) -> None:
    method = info.get("token_endpoint_auth_method")
    if method not in _USABLE_AUTH_METHODS:
        raise MCPOAuthError(
            f"The server registered the client with unsupported auth method {method!r}"
        )
    if method in _SECRET_AUTH_METHODS and not info.get("client_secret"):
        raise MCPOAuthError(
            f"The server registered the client for {method!r} without a secret"
        )


def _json_object(body: str) -> dict[str, Any] | None:
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _tokens(payload: dict[str, Any]) -> OAuthTokens:
    tokens: OAuthTokens = {
        "access_token": str(payload["access_token"]),
        "token_type": str(payload.get("token_type", "Bearer")),
    }
    for key in ("expires_in", "scope", "refresh_token"):
        if key in payload:
            tokens[key] = payload[key]
    return tokens
