"""Where a server's OAuth state lives.

Tokens, client credentials, and the ``state`` / PKCE verifier that a later
callback request redeems.

Port of upstream ``client/do-oauth-client-provider.ts``, with its Durable
Object KV key layout, so state written by a TypeScript agent is read here
(``.design/mcp_engine.md`` §7.1).
"""

import secrets
from abc import ABC, abstractmethod
from typing import Any, TypedDict
from urllib.parse import urlsplit

from .. import _ffi
from ..core.timing import now_ms
from .types import (
    OAuthClientInformation,
    OAuthClientMetadata,
    OAuthTokens,
    StateCheck,
)

__all__ = ("DurableObjectOAuthProvider", "MCPOAuthProvider")

_STATE_LIFETIME_MS = 10 * 60 * 1000
_DELETE_CHUNK = 128


class OAuthDiscoveryState(TypedDict, total=False):
    """What discovery found, kept for the callback's code exchange."""

    authorizationServerUrl: str
    resourceMetadataUrl: str
    resourceMetadata: dict[str, Any]
    authorizationServerMetadata: dict[str, Any]
    resource: str


class MCPOAuthProvider(ABC):
    """Stores one server's OAuth state; override it for another client setup.

    ``server_id`` and ``client_id`` are set by the client before use.

    Parameters
    ----------
    redirect_url
        The OAuth callback URL.
    """

    def __init__(self, redirect_url: str) -> None:
        self.redirect_url = redirect_url
        self.server_id = ""
        """The server this provider stores state for."""
        self.client_id: str | None = None
        """The registered OAuth client id, once there is one."""

    @property
    @abstractmethod
    def client_metadata(self) -> OAuthClientMetadata:
        """``OAuthClientMetadata``: the dynamic registration request."""

    @abstractmethod
    async def tokens(self) -> OAuthTokens | None:
        """Return the stored tokens."""

    @abstractmethod
    async def save_tokens(self, tokens: OAuthTokens) -> None:
        """Store new tokens."""

    @abstractmethod
    async def clear_tokens(self) -> None:
        """Forget the tokens (a refresh failed)."""

    @abstractmethod
    async def client_information(self) -> OAuthClientInformation | None:
        """Return the registered client's credentials."""

    @abstractmethod
    async def save_client_information(self, info: OAuthClientInformation) -> None:
        """Store a registration; sets `client_id`."""

    @abstractmethod
    async def clear_client_information(self) -> None:
        """Forget the registration and its tokens."""

    @abstractmethod
    async def discovery_state(self) -> OAuthDiscoveryState | None:
        """Return what discovery found for the pending authorization."""

    @abstractmethod
    async def save_discovery_state(self, state: OAuthDiscoveryState) -> None:
        """Store what discovery found."""

    @abstractmethod
    async def create_state(self) -> str:
        """Return a new ``state`` parameter (``<nonce>.<server_id>``), stored."""

    @abstractmethod
    async def check_state(self, state: str) -> StateCheck:
        """Return whether ``state`` was issued here and is unexpired.

        Checking doesn't use it up; `consume_state` does, once the callback
        succeeds.
        """

    @abstractmethod
    async def consume_state(self, state: str) -> None:
        """Use ``state`` up."""

    @abstractmethod
    async def save_code_verifier(self, state: str, verifier: str) -> None:
        """Store the PKCE verifier for the authorization ``state`` started."""

    @abstractmethod
    async def code_verifier(self, state: str) -> str | None:
        """Return the PKCE verifier for ``state``."""

    @abstractmethod
    async def delete_code_verifier(self, state: str) -> None:
        """Forget the PKCE verifier for ``state``."""

    @abstractmethod
    async def clear(self) -> None:
        """Forget everything stored for this server."""


class DurableObjectOAuthProvider(MCPOAuthProvider):
    """Keeps OAuth state in Durable Object storage, with dynamic registration.

    Keys are upstream's: ``/{client_name}/{server_id}/{client_id}/token`` and
    so on.

    Parameters
    ----------
    storage
        The Durable Object's ``ctx.storage``.
    client_name
        Prefixes every key, and names the client when it registers.
    redirect_url
        The OAuth callback URL.
    """

    def __init__(self, storage: Any, client_name: str, redirect_url: str) -> None:
        super().__init__(redirect_url)
        self._storage = _ffi.unwrap(storage)
        self.client_name = client_name

    @property
    def client_metadata(self) -> OAuthClientMetadata:
        """``OAuthClientMetadata``: a public client (no secret), as upstream."""
        url = urlsplit(self.redirect_url)
        return {
            "client_name": self.client_name,
            "client_uri": f"{url.scheme}://{url.netloc}",
            "grant_types": ["authorization_code", "refresh_token"],
            "redirect_uris": [self.redirect_url],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

    # Keys

    @property
    def _server_prefix(self) -> str:
        return f"/{self.client_name}/{self.server_id}"

    def _client_key(self, suffix: str) -> str | None:
        if self.client_id is None:
            return None
        return f"{self._server_prefix}/{self.client_id}/{suffix}"

    def _verifier_key(self, state: str) -> str | None:
        nonce = _nonce(state)
        return None if nonce is None else self._client_key(f"code_verifier/{nonce}")

    # Storage

    async def _get(self, key: str | None) -> Any:
        return None if key is None else _ffi.js_to_py(await self._storage.get(key))

    async def _put(self, key: str | None, value: Any) -> None:
        if key is not None:
            await self._storage.put(key, _ffi.py_to_js(value))

    async def _delete(self, *keys: str | None) -> None:
        present = [key for key in keys if key is not None]
        if present:
            await self._storage.delete(_ffi.py_to_js(present))

    # Tokens and client credentials

    async def tokens(self) -> OAuthTokens | None:
        """Return the stored tokens."""
        return await self._get(self._client_key("token"))

    async def save_tokens(self, tokens: OAuthTokens) -> None:
        """Store new tokens; the discovery state is no longer needed."""
        await self._put(self._client_key("token"), tokens)
        await self._delete(f"{self._server_prefix}/oauth_discovery")

    async def clear_tokens(self) -> None:
        """Forget the tokens."""
        await self._delete(self._client_key("token"))

    async def client_information(self) -> OAuthClientInformation | None:
        """Return the registered client's credentials."""
        return await self._get(self._client_key("client_info/"))

    async def save_client_information(self, info: OAuthClientInformation) -> None:
        """Store a registration; sets `client_id`."""
        self.client_id = info["client_id"]
        await self._put(self._client_key("client_info/"), info)

    async def clear_client_information(self) -> None:
        """Forget the registration and its tokens."""
        await self._delete(self._client_key("client_info/"), self._client_key("token"))
        self.client_id = None

    async def discovery_state(self) -> OAuthDiscoveryState | None:
        """Return what discovery found for the pending authorization."""
        return await self._get(f"{self._server_prefix}/oauth_discovery")

    async def save_discovery_state(self, state: OAuthDiscoveryState) -> None:
        """Store what discovery found."""
        await self._put(f"{self._server_prefix}/oauth_discovery", state)

    # The state parameter and the PKCE verifier

    async def create_state(self) -> str:
        """Return a new ``state`` parameter, stored for 10 minutes."""
        nonce = secrets.token_urlsafe(16)
        stored = {"nonce": nonce, "serverId": self.server_id, "createdAt": now_ms()}
        await self._put(f"{self._server_prefix}/state/{nonce}", stored)
        return f"{nonce}.{self.server_id}"

    async def check_state(self, state: str) -> StateCheck:
        """Return whether ``state`` was issued here and is unexpired."""
        parts = _parse_state(state)
        if parts is None:
            return StateCheck(valid=False, error="Invalid state format")
        nonce, server_id = parts
        key = f"{self._server_prefix}/state/{nonce}"
        stored = await self._get(key)
        if stored is None:
            return StateCheck(valid=False, error="State not found or already used")
        if stored.get("serverId") != server_id:
            await self._delete(key)
            return StateCheck(valid=False, error="State serverId mismatch")
        if now_ms() - stored.get("createdAt", 0) > _STATE_LIFETIME_MS:
            await self._delete(key, self._verifier_key(state))
            return StateCheck(valid=False, error="State expired")
        return StateCheck(valid=True)

    async def consume_state(self, state: str) -> None:
        """Use ``state`` up."""
        nonce = _nonce(state)
        if nonce is not None:
            await self._delete(f"{self._server_prefix}/state/{nonce}")

    async def save_code_verifier(self, state: str, verifier: str) -> None:
        """Store the PKCE verifier for ``state``."""
        await self._put(
            self._verifier_key(state), {"verifier": verifier, "createdAt": now_ms()}
        )

    async def code_verifier(self, state: str) -> str | None:
        """Return the PKCE verifier for ``state`` (or upstream's legacy one)."""
        stored = await self._get(self._verifier_key(state))
        if stored is None:
            stored = await self._get(self._client_key("code_verifier"))
        if isinstance(stored, dict):
            return stored.get("verifier")
        return stored if isinstance(stored, str) else None

    async def delete_code_verifier(self, state: str) -> None:
        """Forget the PKCE verifier for ``state``."""
        await self._delete(self._verifier_key(state))

    async def clear(self) -> None:
        """Delete every key stored for this server, 128 at a time."""
        keys = await _ffi.kv_keys(self._storage, f"{self._server_prefix}/")
        for start in range(0, len(keys), _DELETE_CHUNK):
            await self._delete(*keys[start : start + _DELETE_CHUNK])


def _parse_state(state: str) -> tuple[str, str] | None:
    parts = state.split(".")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def _nonce(state: str) -> str | None:
    parts = _parse_state(state)
    return None if parts is None else parts[0]
