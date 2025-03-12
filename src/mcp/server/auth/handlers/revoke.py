from dataclasses import dataclass
from functools import partial
from typing import Literal

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import Response

from mcp.server.auth.errors import (
    InvalidClientError,
    stringify_pydantic_error,
)
from mcp.server.auth.json_response import PydanticJSONResponse
from mcp.server.auth.middleware.client_auth import (
    ClientAuthenticator,
)
from mcp.server.auth.provider import AuthInfo, OAuthServerProvider, RefreshToken


class RevocationRequest(BaseModel):
    """
    # See https://datatracker.ietf.org/doc/html/rfc7009#section-2.1
    """

    token: str
    token_type_hint: Literal["access_token", "refresh_token"] | None = None
    client_id: str
    client_secret: str | None


class RevocationErrorResponse(BaseModel):
    error: Literal["invalid_request",]
    error_description: str | None = None


@dataclass
class RevocationHandler:
    provider: OAuthServerProvider
    client_authenticator: ClientAuthenticator

    async def handle(self, request: Request) -> Response:
        """
        Handler for the OAuth 2.0 Token Revocation endpoint.
        """
        try:
            form_data = await request.form()
            revocation_request = RevocationRequest.model_validate(dict(form_data))
        except ValidationError as e:
            return PydanticJSONResponse(
                status_code=400,
                content=RevocationErrorResponse(
                    error="invalid_request",
                    error_description=stringify_pydantic_error(e),
                ),
            )

        # Authenticate client
        try:
            client = await self.client_authenticator.authenticate(
                revocation_request.client_id, revocation_request.client_secret
            )
        except InvalidClientError as e:
            return PydanticJSONResponse(status_code=401, content=e.error_response())

        loaders = [
            self.provider.load_access_token,
            partial(self.provider.load_refresh_token, client),
        ]
        if revocation_request.token_type_hint == "refresh_token":
            loaders = reversed(loaders)

        token: None | AuthInfo | RefreshToken = None
        for loader in loaders:
            token = await loader(revocation_request.token)
            if token is not None:
                break

        if token and token.client_id == client.client_id:
            # Revoke token
            await self.provider.revoke_token(token)

        # Return successful empty response
        return Response(
            status_code=200,
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
            },
        )
