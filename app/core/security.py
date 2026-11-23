"""API key authentication.

Clients send `X-API-Key: <key>` (or `Authorization: Bearer <key>`).
Keys come from the API_KEYS environment variable. Keys are compared in
constant time and only a short hashed identifier is ever logged or stored.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import ApiError


def key_id(api_key: str) -> str:
    """Short, non-reversible identifier for a key (safe for logs and metrics)."""
    return "key_" + hashlib.sha256(api_key.encode()).hexdigest()[:10]


def extract_api_key(request: Request) -> str | None:
    key = request.headers.get("x-api-key")
    if key:
        return key.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def authenticate(request: Request) -> str:
    """FastAPI dependency. Returns a client identifier used for rate limiting."""
    settings = get_settings()
    if not settings.auth_required:
        client = request.client.host if request.client else "anonymous"
        request.state.client_id = f"anon_{client}"
        return request.state.client_id
    supplied = extract_api_key(request)
    if not supplied:
        raise ApiError(401, "missing_api_key", "Provide an API key in the X-API-Key header.")
    for valid in settings.api_key_list:
        if hmac.compare_digest(supplied.encode(), valid.encode()):
            request.state.client_id = key_id(valid)
            return request.state.client_id
    raise ApiError(401, "invalid_api_key", "The API key is not valid.")
