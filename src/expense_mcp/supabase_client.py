"""Supabase client helpers with per-request token context."""

from __future__ import annotations

import os
from contextvars import ContextVar

from supabase import Client, create_client

# Per-request JWT — set by the API-key middleware on each incoming request
_request_token: ContextVar[str] = ContextVar("request_token", default="")


def set_request_token(token: str) -> None:
    """Inject the user JWT for the current async context (called by middleware)."""
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
      1. Per-request context (set by API-key middleware — production)
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
