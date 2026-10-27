"""An MCP SDK server behind the SDK's own OAuth authorization server.

The provider approves every authorization at once, so a test follows the
authorization URL and gets the callback redirect straight back.
"""

import secrets
import time
from typing import Any

from _servers import demo_server
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

ISSUER = "http://127.0.0.1:8000"


class InMemoryAuthProvider:
    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}
        self.authorize_params: list[AuthorizationParams] = []
        self.refreshes = 0

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        assert client_info.client_id is not None
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        self.authorize_params.append(params)
        code = secrets.token_urlsafe(16)
        assert client.client_id is not None
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + 300,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(
            str(params.redirect_uri), code=code, state=params.state
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        del self.codes[authorization_code.code]
        return self._issue(
            client, authorization_code.scopes, authorization_code.resource
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        self.refreshes += 1
        del self.refresh[refresh_token.token]
        return self._issue(
            client, scopes or refresh_token.scopes, refresh_token.resource
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        return self.access.get(token)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.access.pop(token.token, None)

    async def exchange_identity_assertion(
        self, client: OAuthClientInformationFull, params: Any
    ) -> OAuthToken:
        raise NotImplementedError

    def _issue(
        self,
        client: OAuthClientInformationFull,
        scopes: list[str],
        resource: str | None,
    ) -> OAuthToken:
        assert client.client_id is not None
        access, refresh = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        self.access[access] = AccessToken(
            token=access, client_id=client.client_id, scopes=scopes, resource=resource
        )
        self.refresh[refresh] = RefreshToken(
            token=refresh, client_id=client.client_id, scopes=scopes, resource=resource
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=3600,
            refresh_token=refresh,
            scope=" ".join(scopes) or None,
        )


def oauth_server(provider: InMemoryAuthProvider) -> MCPServer:
    server = demo_server()
    secured = MCPServer(
        "Secured",
        version="1.0.0",
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=ISSUER,
            resource_server_url=f"{ISSUER}/mcp",
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=["read"], default_scopes=["read"]
            ),
            required_scopes=["read"],
            validate_token_resource=True,
        ),
    )
    for tool in server._tool_manager.list_tools():
        secured._tool_manager._tools[tool.name] = tool
    return secured
