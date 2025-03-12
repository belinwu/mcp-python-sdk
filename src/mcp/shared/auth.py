from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field


class OAuthToken(BaseModel):
    """
    See https://datatracker.ietf.org/doc/html/rfc6749#section-5.1
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int | None = None
    scope: str | None = None
    refresh_token: str | None = None


class InvalidScopeError(Exception):
    def __init__(self, message: str):
        self.message = message


class InvalidRedirectUriError(Exception):
    def __init__(self, message: str):
        self.message = message


class OAuthClientMetadata(BaseModel):
    """
    RFC 7591 OAuth 2.0 Dynamic Client Registration metadata.
    See https://datatracker.ietf.org/doc/html/rfc7591#section-2
    for the full specification.
    """

    redirect_uris: list[AnyHttpUrl] = Field(..., min_length=1)
    # token_endpoint_auth_method: this implementation only supports none &
    # client_secret_post;
    # ie: we do not support client_secret_basic
    token_endpoint_auth_method: Literal["none", "client_secret_post"] = (
        "client_secret_post"
    )
    # grant_types: this implementation only supports authorization_code & refresh_token
    grant_types: list[Literal["authorization_code", "refresh_token"]] = [
        "authorization_code"
    ]
    # this implementation only supports code; ie: it does not support implicit grants
    response_types: list[Literal["code"]] = ["code"]
    scope: str | None = None

    # these fields are currently unused, but we support & store them for potential
    # future use
    client_name: str | None = None
    client_uri: AnyHttpUrl | None = None
    logo_uri: AnyHttpUrl | None = None
    contacts: list[str] | None = None
    tos_uri: AnyHttpUrl | None = None
    policy_uri: AnyHttpUrl | None = None
    jwks_uri: AnyHttpUrl | None = None
    jwks: Any | None = None
    software_id: str | None = None
    software_version: str | None = None

    def validate_scope(self, requested_scope: str | None) -> list[str] | None:
        if requested_scope is None:
            return None
        requested_scopes = requested_scope.split(" ")
        allowed_scopes = [] if self.scope is None else self.scope.split(" ")
        for scope in requested_scopes:
            if scope not in allowed_scopes:
                raise InvalidScopeError(f"Client was not registered with scope {scope}")
        return requested_scopes

    def validate_redirect_uri(self, redirect_uri: AnyHttpUrl | None) -> AnyHttpUrl:
        if redirect_uri is not None:
            # Validate redirect_uri against client's registered redirect URIs
            if redirect_uri not in self.redirect_uris:
                raise InvalidRedirectUriError(
                    f"Redirect URI '{redirect_uri}' not registered for client"
                )
            return redirect_uri
        elif len(self.redirect_uris) == 1:
            return self.redirect_uris[0]
        else:
            raise InvalidRedirectUriError(
                "redirect_uri must be specified when client "
                "has multiple registered URIs"
            )


class OAuthClientInformationFull(OAuthClientMetadata):
    """
    RFC 7591 OAuth 2.0 Dynamic Client Registration full response
    (client information plus metadata).
    """

    client_id: str
    client_secret: str | None = None
    client_id_issued_at: int | None = None
    client_secret_expires_at: int | None = None


class OAuthMetadata(BaseModel):
    """
    RFC 8414 OAuth 2.0 Authorization Server Metadata.
    See https://datatracker.ietf.org/doc/html/rfc8414#section-2
    """

    issuer: AnyHttpUrl
    authorization_endpoint: AnyHttpUrl
    token_endpoint: AnyHttpUrl
    registration_endpoint: AnyHttpUrl | None = None
    scopes_supported: list[str] | None = None
    response_types_supported: list[Literal["code"]] = ["code"]
    response_modes_supported: list[Literal["query", "fragment"]] | None = None
    grant_types_supported: (
        list[Literal["authorization_code", "refresh_token"]] | None
    ) = None
    token_endpoint_auth_methods_supported: (
        list[Literal["none", "client_secret_post"]] | None
    ) = None
    token_endpoint_auth_signing_alg_values_supported: None = None
    service_documentation: AnyHttpUrl | None = None
    ui_locales_supported: list[str] | None = None
    op_policy_uri: AnyHttpUrl | None = None
    op_tos_uri: AnyHttpUrl | None = None
    revocation_endpoint: AnyHttpUrl | None = None
    revocation_endpoint_auth_methods_supported: (
        list[Literal["client_secret_post"]] | None
    ) = None
    revocation_endpoint_auth_signing_alg_values_supported: None = None
    introspection_endpoint: AnyHttpUrl | None = None
    introspection_endpoint_auth_methods_supported: (
        list[Literal["client_secret_post"]] | None
    ) = None
    introspection_endpoint_auth_signing_alg_values_supported: None = None
    code_challenge_methods_supported: list[Literal["S256"]] | None = None
