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


def get_anon_client() -> Client:
    """Unauthenticated Supabase client (registration / login only)."""
    url, key = _url_and_key()
    return create_client(url, key)


def get_client_for_api_key(api_key: str) -> Client:
    """Validate API key, exchange refresh token for a fresh JWT, return authenticated client.

    Called on every tool invocation — stateless, restart-safe.
    """
    url, key = _url_and_key()
    anon = create_client(url, key)

    # Validate key and retrieve stored refresh token
    res = anon.rpc("fn_validate_api_key", {"p_api_key": api_key}).execute()
    data = res.data
    if isinstance(data, list):
        data = data[0] if data else {}
    if not data or data.get("status") != "success":
        raise RuntimeError(
            "Invalid or expired API key. "
            "Call login_get_api_key() to get a new one."
        )

    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError(
            "No session stored for this API key. "
            "Call login_get_api_key() once to save your session."
        )

    # Exchange refresh token → fresh access token
    session_res = anon.auth.refresh_session(refresh_token)
    if not session_res.session:
        raise RuntimeError(
            "Session expired. Call login_get_api_key() to get a fresh session."
        )

    access_token = session_res.session.access_token
    new_refresh = session_res.session.refresh_token

    # Build authenticated client
    authed = create_client(url, key)
    authed.postgrest.auth(access_token)

    # Persist the rotated refresh token via RPC (bypasses RLS update restriction)
    authed.rpc("fn_update_api_key_refresh_token", {
        "p_api_key": api_key,
        "p_refresh_token": new_refresh,
    }).execute()

    return authed


def get_client_for_env_token() -> Client:
    """Authenticated client using SUPABASE_ACCESS_TOKEN env var (local dev only)."""
    url, key = _url_and_key()
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Not authenticated. "
            "New users: call register_new_user(). "
            "Existing users: call login_get_api_key()."
        )
    client = create_client(url, key)
    client.postgrest.auth(token)
    return client


def get_jwt_from_client(client: Client) -> str:
    """Extract the JWT from an authenticated client's postgrest session."""
    auth_header = client.postgrest.session.headers.get("Authorization", "")
    return auth_header.replace("Bearer ", "").strip()
