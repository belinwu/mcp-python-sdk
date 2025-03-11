"""
Handler for OAuth 2.0 Token Revocation.

Corresponds to TypeScript file: src/server/auth/handlers/revoke.ts
"""

from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import Response
from pydantic import ValidationError

from mcp.server.auth.errors import (
    InvalidRequestError,
    ServerError,
    OAuthError,
)
from mcp.server.auth.middleware import client_auth
from mcp.server.auth.provider import OAuthServerProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthTokenRevocationRequest
from mcp.server.auth.middleware.client_auth import ClientAuthRequest, ClientAuthenticator

class RevocationRequest(OAuthTokenRevocationRequest, ClientAuthRequest):
    pass

def create_revocation_handler(provider: OAuthServerProvider, client_authenticator: ClientAuthenticator) -> Callable:
    """
    Create a handler for OAuth 2.0 Token Revocation.
    
    Corresponds to revocationHandler in src/server/auth/handlers/revoke.ts
    
    Args:
        provider: The OAuth server provider
        
    Returns:
        A Starlette endpoint handler function
    """
    
    async def revocation_handler(request: Request) -> Response:
        """
        Handler for the OAuth 2.0 Token Revocation endpoint.
        """
        try:
            revocation_request = RevocationRequest.model_validate_json(await request.body())
        except ValidationError as e:
            raise InvalidRequestError(f"Invalid request body: {e}")
        
        # Authenticate client
        client_auth_result = await client_authenticator(revocation_request)
        
        # Revoke token
        if provider.revoke_token:
            await provider.revoke_token(client_auth_result, revocation_request)
        
        # Return successful empty response
        return Response(
            status_code=200,
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
            }
        )
    
    return revocation_handler