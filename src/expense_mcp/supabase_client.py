"""Supabase client helpers."""

from __future__ import annotations

import os

from supabase import Client, create_client


def _url_and_key() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY must be set.")
    return url, key


def require_access_token() -> str:
    """Return the current user's JWT.

    Production: token is injected per-request via API-key middleware.
    Local dev: falls back to SUPABASE_ACCESS_TOKEN env var.
    """
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
    """Unauthenticated Supabase client (used only for registration / login RPCs)."""
    url, key = _url_and_key()
    return create_client(url, key)
