"""
Handler for OAuth 2.0 Authorization endpoint.

Corresponds to TypeScript file: src/server/auth/handlers/authorize.ts
"""

from typing import Callable, Literal, Optional, Union
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from pydantic import AnyHttpUrl, AnyUrl, BaseModel, Field, RootModel, ValidationError
from starlette.datastructures import FormData, QueryParams
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from mcp.server.auth.errors import (
    InvalidClientError,
    InvalidRequestError,
    OAuthError,
    stringify_pydantic_error,
)
from mcp.server.auth.provider import AuthorizationParams, OAuthServerProvider, construct_redirect_uri
from mcp.shared.auth import OAuthClientInformationFull
from mcp.server.auth.json_response import PydanticJSONResponse

import logging

logger = logging.getLogger(__name__)


class AuthorizationRequest(BaseModel):
    # See https://datatracker.ietf.org/doc/html/rfc6749#section-4.1.1
    client_id: str = Field(..., description="The client ID")
    redirect_uri: AnyHttpUrl | None = Field(
        None, description="URL to redirect to after authorization"
    )

    # see OAuthClientMetadata; we only support `code`
    response_type: Literal["code"] = Field(
        ..., description="Must be 'code' for authorization code flow"
    )
    code_challenge: str = Field(..., description="PKCE code challenge")
    code_challenge_method: Literal["S256"] = Field(
        "S256", description="PKCE code challenge method, must be S256"
    )
    state: Optional[str] = Field(None, description="Optional state parameter")
    scope: Optional[str] = Field(
        None,
        description="Optional scope; if specified, should be "
        "a space-separated list of scope strings",
    )
    


def validate_scope(
    requested_scope: str | None, client: OAuthClientInformationFull
) -> list[str] | None:
    if requested_scope is None:
        return None
    requested_scopes = requested_scope.split(" ")
    allowed_scopes = [] if client.scope is None else client.scope.split(" ")
    for scope in requested_scopes:
        if scope not in allowed_scopes:
            raise InvalidRequestError(f"Client was not registered with scope {scope}")
    return requested_scopes


def validate_redirect_uri(
    redirect_uri: AnyHttpUrl | None, client: OAuthClientInformationFull
) -> AnyHttpUrl:
    if redirect_uri is not None:
        # Validate redirect_uri against client's registered redirect URIs
        if redirect_uri not in client.redirect_uris:
            raise InvalidRequestError(
                f"Redirect URI '{redirect_uri}' not registered for client"
            )
        return redirect_uri
    elif len(client.redirect_uris) == 1:
        return client.redirect_uris[0]
    else:
        raise InvalidRequestError(
            "redirect_uri must be specified when client has multiple registered URIs"
        )
ErrorCode = Literal[
        "invalid_request",
        "unauthorized_client",
        "access_denied",
        "unsupported_response_type",
        "invalid_scope",
        "server_error",
        "temporarily_unavailable"
    ]
class ErrorResponse(BaseModel):
    error: ErrorCode
    error_description: str
    error_uri: Optional[AnyUrl] = None
    # must be set if provided in the request
    state: Optional[str]

def best_effort_extract_string(key: str, params: None | FormData | QueryParams) -> Optional[str]:
    if params is None:
        return None
    value = params.get(key)
    if isinstance(value, str):
        return value
    return None

class AnyHttpUrlModel(RootModel):
    root: AnyHttpUrl


def create_authorization_handler(provider: OAuthServerProvider) -> Callable:
    async def authorization_handler(request: Request) -> Response:
        # implements authorization requests for grant_type=code;
        # see https://datatracker.ietf.org/doc/html/rfc6749#section-4.1.1

        state = None
        redirect_uri = None
        client = None
        params = None

        async def error_response(error: ErrorCode, error_description: str, attempt_load_client: bool = True):
            nonlocal client, redirect_uri, state
            if client is None and attempt_load_client:
                # make last-ditch attempt to load the client
                client_id = best_effort_extract_string("client_id", params)
                client = client_id and await provider.clients_store.get_client(client_id)
            if redirect_uri is None and client:
                # make last-ditch effort to load the redirect uri
                if params is not None and "redirect_uri" not in params:
                    raw_redirect_uri = None
                else:
                    raw_redirect_uri = AnyHttpUrlModel.model_validate(best_effort_extract_string("redirect_uri", params)).root
                try:
                    redirect_uri = validate_redirect_uri(raw_redirect_uri, client)
                except (ValidationError, InvalidRequestError):
                    pass
            if state is None:
                # make last-ditch effort to load state
                state = best_effort_extract_string("state", params)

            error_resp = ErrorResponse(
                error=error,
                error_description=error_description,
                state=state,
            )

            if redirect_uri and client:
                return RedirectResponse(
                    url=construct_redirect_uri(str(redirect_uri), **error_resp.model_dump(exclude_none=True)),
                    status_code=302,
                    headers={"Cache-Control": "no-store"},
                )
            else:
                return PydanticJSONResponse(
                    status_code=400,
                    content=error_resp,
                    headers={"Cache-Control": "no-store"},
                )
        
        try:
            # Parse request parameters
            if request.method == "GET":
                # Convert query_params to dict for pydantic validation
                params = request.query_params
            else:
                # Parse form data for POST requests
                params = await request.form()
                
            # Save state if it exists, even before validation
            state = best_effort_extract_string("state", params)
                
            try:
                auth_request = AuthorizationRequest.model_validate(params)
                state = auth_request.state  # Update with validated state
            except ValidationError as validation_error:
                error: ErrorCode = "invalid_request"
                for e in validation_error.errors():
                    if e['loc'] == ('response_type',) and e['type'] == 'literal_error':
                        error = "unsupported_response_type"
                        break
                return await error_response(error, stringify_pydantic_error(validation_error))

            # Get client information
            client = await provider.clients_store.get_client(auth_request.client_id)
            if not client:
                # For client_id validation errors, return direct error (no redirect)
                return await error_response(
                    error="invalid_request",
                    error_description=f"Client ID '{auth_request.client_id}' not found",
                    attempt_load_client=False,
                )

                
            # Validate redirect_uri against client's registered URIs
            try:
                redirect_uri = validate_redirect_uri(auth_request.redirect_uri, client)
            except InvalidRequestError as validation_error:
                # For redirect_uri validation errors, return direct error (no redirect)
                return await error_response(
                    error="invalid_request",
                    error_description=validation_error.message,
                )
                
            # Validate scope - for scope errors, we can redirect
            try:
                scopes = validate_scope(auth_request.scope, client)
            except InvalidRequestError as validation_error:
                # For scope errors, redirect with error parameters
                return await error_response(
                    error="invalid_scope",
                    error_description=validation_error.message,
                )
                
            # Setup authorization parameters
            auth_params = AuthorizationParams(
                state=state,
                scopes=scopes,
                code_challenge=auth_request.code_challenge,
                redirect_uri=redirect_uri,
            )
            
            # Let the provider pick the next URI to redirect to
            response = RedirectResponse(
                url="", status_code=302, headers={"Cache-Control": "no-store"}
            )
            response.headers["location"] = await provider.authorize(
                client, auth_params
            )
            return response
                
        except Exception as validation_error:
            # Catch-all for unexpected errors
            logger.exception("Unexpected error in authorization_handler", exc_info=validation_error)
            return await error_response(error="server_error", error_description="An unexpected error occurred")

    return authorization_handler


def create_error_redirect(
    redirect_uri: AnyUrl, error: Union[Exception, ErrorResponse]
) -> str:
    parsed_uri = urlparse(str(redirect_uri))
    
    if isinstance(error, ErrorResponse):
        # Convert ErrorResponse to dict
        error_dict = error.model_dump(exclude_none=True)
        query_params = {}
        for key, value in error_dict.items():
            if value is not None:
                if key == "error_uri" and hasattr(value, "__str__"):
                    query_params[key] = str(value)
                else:
                    query_params[key] = value
                    
    elif isinstance(error, OAuthError):
        query_params = {"error": error.error_code, "error_description": str(error)}
    else:
        query_params = {
            "error": "server_error",
            "error_description": "An unknown error occurred",
        }

    new_query = urlencode(query_params)
    if parsed_uri.query:
        new_query = f"{parsed_uri.query}&{new_query}"

    return urlunparse(parsed_uri._replace(query=new_query))
