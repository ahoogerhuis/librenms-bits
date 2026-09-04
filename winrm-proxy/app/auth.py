"""Request authentication.

Two modes, matching Settings.auth_mode:

TOKEN: static pre-shared bearer token, checked here at the app layer via
constant-time compare.

MTLS: the client-certificate requirement and CA-chain verification is
enforced by the TLS layer itself (uvicorn's ssl_cert_reqs=CERT_REQUIRED
+ ssl_ca_certs=<pinned CA>, configured in main.py's run() from
Settings) -- a connection presenting no cert, or one not signed by the
pinned CA, never completes its TLS handshake and never reaches this
code at all. That's the real security boundary, and it's the standard
mechanism for this rather than hand-rolled peer-certificate inspection
at the application layer (which Starlette/uvicorn don't cleanly expose
in the first place). This module's mtls path exists so main.py always
calls one uniform dependency regardless of auth_mode; there is nothing
further to check here in that mode by construction.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request

from app.config import Settings


def require_auth(settings: Settings):
    """Build a FastAPI dependency bound to the loaded Settings."""

    def _dependency(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        if settings.auth_mode == "token":
            _check_token(authorization, settings.token)
        # auth_mode == "mtls": nothing to check here, see module docstring.

    return _dependency


def _check_token(authorization: str | None, expected_token: str) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    provided = authorization.removeprefix("Bearer ").strip()

    # hmac.compare_digest, not ==, to avoid leaking token length/prefix
    # via response-timing.
    if not hmac.compare_digest(provided.encode(), expected_token.encode()):
        raise HTTPException(status_code=401, detail="invalid token")
