"""OAuth bearer-token verification for the public Streamable HTTP transport.

The local Codex stdio transport never imports or requires these settings. Public
mode is deliberately fail-closed: all required OAuth and single-tenant settings
must be present before the HTTP listener can start.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

import anyio
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings


@dataclass(frozen=True)
class PublicAuthConfig:
    public_url: str
    issuer_url: str
    jwks_url: str
    audience: str
    required_scopes: tuple[str, ...]
    allowed_subjects: frozenset[str]
    algorithms: tuple[str, ...]


def _require_https(name: str, value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError(f"{name} must be an absolute HTTPS URL")
    return value.rstrip("/") if not value.endswith("/mcp") else value


def _split(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.replace(",", " ").split() if part)


def load_public_auth_config(env: Mapping[str, str] | None = None) -> PublicAuthConfig:
    """Load and validate the mandatory public-server environment."""
    values = os.environ if env is None else env
    required = ("MCP_PUBLIC_URL", "OAUTH_ISSUER_URL", "OAUTH_JWKS_URL", "OAUTH_ALLOWED_SUBJECTS")
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise RuntimeError("Public MCP mode requires: " + ", ".join(missing))

    public_url = _require_https("MCP_PUBLIC_URL", values["MCP_PUBLIC_URL"].strip())
    if not public_url.endswith("/mcp"):
        raise RuntimeError("MCP_PUBLIC_URL must include the final /mcp path")

    scopes = _split(values.get("OAUTH_REQUIRED_SCOPES", "taobao:mcp"))
    subjects = frozenset(_split(values["OAUTH_ALLOWED_SUBJECTS"]))
    algorithms = _split(values.get("OAUTH_JWT_ALGORITHMS", "RS256 ES256"))
    if not scopes or not subjects or not algorithms:
        raise RuntimeError("OAuth scopes, allowed subjects, and JWT algorithms must not be empty")

    return PublicAuthConfig(
        public_url=public_url,
        issuer_url=_require_https("OAUTH_ISSUER_URL", values["OAUTH_ISSUER_URL"].strip()),
        jwks_url=_require_https("OAUTH_JWKS_URL", values["OAUTH_JWKS_URL"].strip()),
        audience=values.get("OAUTH_AUDIENCE", public_url).strip(),
        required_scopes=scopes,
        allowed_subjects=subjects,
        algorithms=algorithms,
    )


class JwtTokenVerifier:
    """Verify JWT access tokens issued by the configured OAuth 2.1 provider."""

    def __init__(self, config: PublicAuthConfig) -> None:
        self.config = config
        self._jwks = PyJWKClient(config.jwks_url, cache_keys=True)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = await anyio.to_thread.run_sync(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.config.algorithms),
                audience=self.config.audience,
                issuer=self.config.issuer_url,
                options={"require": ["exp", "sub"]},
            )
        except Exception:
            return None

        subject = str(claims.get("sub", ""))
        if subject not in self.config.allowed_subjects:
            return None

        raw_scopes = claims.get("scope", claims.get("scp", ""))
        scopes = list(_split(raw_scopes)) if isinstance(raw_scopes, str) else list(raw_scopes or [])
        if not set(self.config.required_scopes).issubset(scopes):
            return None

        client_id = str(claims.get("client_id") or claims.get("azp") or subject)
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.config.audience,
            subject=subject,
            claims=dict(claims),
        )


def build_public_auth(config: PublicAuthConfig) -> tuple[AuthSettings, JwtTokenVerifier]:
    settings = AuthSettings(
        issuer_url=config.issuer_url,
        resource_server_url=config.public_url,
        required_scopes=list(config.required_scopes),
    )
    return settings, JwtTokenVerifier(config)
