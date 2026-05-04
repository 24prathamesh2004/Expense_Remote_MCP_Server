"""Supabase client helpers with per-request token context."""

from __future__ import annotations

import os
from contextvars import ContextVar

from supabase import Client, create_client

# Per-request JWT — set by resolve_api_key() before each tool call
_request_token: ContextVar[str] = ContextVar("request_token", default="")


def set_request_token(token: str) -> None:
    _request_token.set(token)


def _url_and_key() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set.")
    return url, key


def require_access_token() -> str:
    """Return the JWT for the current request.

    Priority:
      1. Per-request context (set by resolve_api_key)
      2. SUPABASE_ACCESS_TOKEN env var (local dev only)
    """
    token = _request_token.get()
    if token:
        return token
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Not authenticated. "
            "New users: call register_new_user(). "
            "Existing users: call login_get_api_key()."
        )
    return token


def get_user_client() -> Client:
    """Authenticated Supabase client (RLS enforced via user JWT)."""
    url, key = _url_and_key()
    client = create_client(url, key)
    client.postgrest.auth(require_access_token())
    return client


def get_anon_client() -> Client:
    """Unauthenticated Supabase client (registration / login only)."""
    url, key = _url_and_key()
    return create_client(url, key)


def resolve_api_key(api_key: str, token_cache: dict[str, str]) -> str | None:
    """Resolve an API key to a JWT.

    1. Check in-memory cache (fast path).
    2. On cache miss (server restart), use stored refresh_token to get a new JWT.
    Returns the JWT or None if the key is invalid.
    """
    # Fast path: token already cached
    token = token_cache.get(api_key)
    if token:
        set_request_token(token)
        return token

    # Slow path: cache miss after server restart — use refresh token from DB
    try:
        url, key = _url_and_key()
        client = create_client(url, key)

        # Validate key and get refresh token
        res = client.rpc("fn_validate_api_key", {"p_api_key": api_key}).execute()
        data = res.data
        if isinstance(data, list):
            data = data[0] if data else {}

        if not data or data.get("status") != "success":
            return None

        refresh_token = data.get("refresh_token")
        if not refresh_token:
            return None

        # Exchange refresh token for a new access token
        refresh_res = client.auth.refresh_session(refresh_token)
        if not refresh_res.session:
            return None

        new_token = refresh_res.session.access_token
        new_refresh = refresh_res.session.refresh_token

        # Update cache and DB with new tokens
        token_cache[api_key] = new_token
        set_request_token(new_token)

        # Persist new refresh token
        client.postgrest.auth(new_token)
        client.table("api_keys").update({"refresh_token": new_refresh}).eq("api_key", api_key).execute()

        return new_token

    except Exception:
        return None
