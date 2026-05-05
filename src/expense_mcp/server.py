"""
FastMCP expense + group finance (Supabase).

Auth model: every tool accepts api_key as the first argument.
No in-memory session state — each call validates the key and exchanges
the stored refresh token for a fresh JWT. Stateless and restart-safe.

Env: SUPABASE_URL, SUPABASE_ANON_KEY
     SUPABASE_ACCESS_TOKEN  (local dev only, skips api_key param)

SQL migrations (run in order):
  002_collaborative_finance.sql
  005_settlement_recording.sql
  007b_fix_registration.sql
  008_api_key_refresh_token.sql
  004_pending_approvals_optimization.sql  (optional, for performance)
  006_security_audit.sql                  (optional, for hardening)
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

from expense_mcp.jwt_sub import jwt_subject
from expense_mcp.settlements import accumulate_group_balances, simplify_debts
from expense_mcp.supabase_client import (
    get_anon_client,
    get_client_for_api_key,
    get_client_for_env_token,
    get_jwt_from_client,
)

load_dotenv()

mcp = FastMCP("Expense (Supabase + Groups)")

_ROOT = Path(__file__).resolve().parent.parent.parent
CATEGORIES_PATH = Path(
    os.environ.get("EXPENSE_CATEGORIES_PATH", str(_ROOT / "categories.json"))
)

_DEFAULT_CATEGORIES = {
    "categories": [
        "Food & Dining", "Groceries", "Transportation", "Fuel & Vehicle",
        "Shopping", "Entertainment", "Bills & Utilities", "Mobile & Internet",
        "Healthcare", "Travel", "Education", "Rent", "EMI & Loans",
        "Investments", "Donations", "Personal Care", "Household", "Business", "Other",
    ]
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _err(message: str) -> dict[str, str]:
    return {"status": "error", "message": message}


def _jsonable_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert Decimal values to float for JSON serialisation."""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()}


def _get_client(api_key: str):
    """Return an authenticated Supabase client.

    Production: validates api_key against DB, exchanges refresh token for JWT.
    Local dev:  uses SUPABASE_ACCESS_TOKEN env var (api_key is empty string).
    """
    if api_key:
        return get_client_for_api_key(api_key)
    return get_client_for_env_token()


def _with_pending_hint(payload: Any, client) -> dict[str, Any]:
    """Wrap payload and attach pending-approvals summary.

    Accepts the already-authenticated client to avoid a second auth round-trip.
    """
    try:
        uid = jwt_subject(get_jwt_from_client(client))
        base: dict[str, Any] = {"result": payload}
        res = client.rpc("fn_get_pending_count", {"p_user_id": uid}).execute()
        if res.data:
            row = res.data[0]
            count = row.get("count", 0)
            if count > 0:
                base["pending_approvals_summary"] = {
                    "count": count,
                    "items": row.get("sample") or [],
                }
        return base
    except Exception:
        return {"result": payload}


# ---------------------------------------------------------------------------
# Auth / Registration tools  (no api_key needed)
# ---------------------------------------------------------------------------

@mcp.tool()
def register_new_user(email: str, password: str, full_name: str = "") -> dict[str, Any]:
    """
    🆕 One-time self-service registration. Creates your account and returns an API key.

    After this call:
    1. Copy the returned api_key.
    2. Add it to your Claude Desktop config under headers → X-API-Key.
    3. Restart Claude Desktop — you're done forever.

    Args:
        email: Your email address.
        password: Min 8 characters.
        full_name: Optional display name.
    """
    try:
        client = get_anon_client()
        auth_res = client.auth.sign_up({
            "email": email,
            "password": password,
            "options": {"data": {"full_name": full_name or ""}},
        })
        if not auth_res.user:
            return _err("Registration failed: could not create user.")
        if not auth_res.session:
            return _err(
                "User created but no session returned. "
                "Disable 'Confirm email' in Supabase → Authentication → Providers → Email."
            )
        user_id = str(auth_res.user.id)
        access_token = auth_res.session.access_token
        refresh_token = auth_res.session.refresh_token or ""

        client.postgrest.auth(access_token)
        res = client.rpc("fn_generate_api_key", {
            "p_user_id": user_id,
            "p_key_name": "Default Key",
            "p_refresh_token": refresh_token,
        }).execute()
        api_key = getattr(res, "data", None)
        if not api_key:
            return _err("User created but API key generation failed.")

        return {
            "status": "success",
            "user_id": user_id,
            "email": email,
            "api_key": api_key,
            "message": "Registration successful! Save your API key — it won't be shown again.",
        }
    except Exception as e:
        return _err(f"Registration failed: {e!s}")


@mcp.tool()
def login_get_api_key(email: str, password: str) -> dict[str, Any]:
    """
    🔑 Get your API key. Use this if you lost your key or are setting up a new device.

    Args:
        email: Your registered email.
        password: Your password.
    """
    try:
        client = get_anon_client()
        auth_res = client.auth.sign_in_with_password({"email": email, "password": password})
        if not auth_res.user or not auth_res.session:
            return _err("Invalid email or password.")

        user_id = str(auth_res.user.id)
        access_token = auth_res.session.access_token
        refresh_token = auth_res.session.refresh_token or ""

        client.postgrest.auth(access_token)

        # Get existing active key or generate a new one
        existing = (
            client.table("api_keys")
            .select("api_key")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if existing.data:
            api_key = existing.data[0]["api_key"]
            # Update refresh token via RPC so future calls work
            client.rpc("fn_update_api_key_refresh_token", {
                "p_api_key": api_key,
                "p_refresh_token": refresh_token,
            }).execute()
        else:
            res = client.rpc("fn_generate_api_key", {
                "p_user_id": user_id,
                "p_key_name": "Login Key",
                "p_refresh_token": refresh_token,
            }).execute()
            api_key = getattr(res, "data", None)
            if not api_key:
                return _err("Login succeeded but API key generation failed.")

        return {
            "status": "success",
            "user_id": user_id,
            "email": email,
            "api_key": api_key,
            "message": "Login successful! Use this API key in your Claude Desktop config.",
        }
    except Exception as e:
        return _err(f"Login failed: {e!s}")


@mcp.tool()
def whoami(api_key: str = "") -> dict[str, Any]:
    """Return the current user's id. Confirms authentication is working."""
    try:
        client = _get_client(api_key)
        return {"status": "success", "user_id": jwt_subject(get_jwt_from_client(client))}
    except Exception as e:
        return _err(str(e))


@mcp.tool()
def revoke_my_api_key(api_key: str) -> dict[str, Any]:
    """🚫 Revoke a compromised API key. Call login_get_api_key() afterwards to get a new one."""
    try:
        client = _get_client(api_key)
        res = client.rpc("fn_revoke_api_key", {"p_api_key": api_key}).execute()
        data = getattr(res, "data", {})
        return data if isinstance(data, dict) else _err("Unexpected response")
    except Exception as e:
        return _err(f"Revoke failed: {e!s}")


# ---------------------------------------------------------------------------
# Personal expense tools
# ---------------------------------------------------------------------------

@mcp.tool()
def add_expense(
    api_key: str,
    date: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Add a personal expense (no group). Amount in INR. Date format: YYYY-MM-DD."""
    try:
        client = _get_client(api_key)
        uid = jwt_subject(get_jwt_from_client(client))
        res = client.table("transactions").insert({
            "submitted_by": uid,
            "payer_id": uid,
            "expense_date": date,
            "amount": amount,
            "category": category,
            "subcategory": subcategory or "",
            "note": note or "",
            "status": "approved",
            "group_id": None,
        }).execute()
        if not res.data:
            return _with_pending_hint(_err("Insert returned no data."), client)
        return _with_pending_hint({
            "status": "success",
            "id": str(res.data[0].get("id", "")),
            "message": "Expense added successfully",
        }, client)
    except Exception as e:
        return {"result": _err(f"Database error: {e!s}")}


@mcp.tool()
def list_expenses(api_key: str, start_date: str, end_date: str) -> dict[str, Any]:
    """List personal expenses in an inclusive date range (YYYY-MM-DD)."""
    try:
        client = _get_client(api_key)
        res = (
            client.table("transactions")
            .select("id,expense_date,amount,category,subcategory,note,created_at,status")
            .is_("group_id", None)
            .gte("expense_date", start_date)
            .lte("expense_date", end_date)
            .order("expense_date", desc=True)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])], client)
    except Exception as e:
        return {"result": _err(f"Error listing expenses: {e!s}")}


@mcp.tool()
def summarize(
    api_key: str,
    start_date: str,
    end_date: str,
    category: str | None = None,
) -> dict[str, Any]:
    """Personal spending totals by category for a date range."""
    try:
        client = _get_client(api_key)
        q = (
            client.table("transactions")
            .select("category,amount")
            .is_("group_id", None)
            .gte("expense_date", start_date)
            .lte("expense_date", end_date)
        )
        if category:
            q = q.eq("category", category)
        rows = q.execute().data or []
        buckets: dict[str, dict[str, Any]] = {}
        for r in rows:
            c = r.get("category") or "Other"
            amt = float(r.get("amount") or 0)
            buckets.setdefault(c, {"total_amount": 0.0, "count": 0})
            buckets[c]["total_amount"] += amt
            buckets[c]["count"] += 1
        out = [
            {"category": c, "total_amount": round(v["total_amount"], 2), "count": v["count"]}
            for c, v in sorted(buckets.items(), key=lambda x: -x[1]["total_amount"])
        ]
        return _with_pending_hint(out, client)
    except Exception as e:
        return {"result": _err(f"Error summarizing: {e!s}")}


# ---------------------------------------------------------------------------
# Group management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def create_group(api_key: str, name: str, kind: str = "trip") -> dict[str, Any]:
    """Create a group and add caller as owner. kind: trip | family | team | personal_mirror."""
    try:
        client = _get_client(api_key)
        res = client.rpc("fn_create_group", {"p_name": name, "p_kind": kind}).execute()
        gid = getattr(res, "data", None)
        return _with_pending_hint({"status": "success", "group_id": str(gid) if gid else None}, client)
    except Exception as e:
        return {"result": _err(f"fn_create_group: {e!s}")}


@mcp.tool()
def list_my_groups(api_key: str) -> dict[str, Any]:
    """List all groups the signed-in user belongs to."""
    try:
        client = _get_client(api_key)
        uid = jwt_subject(get_jwt_from_client(client))
        gm = client.table("group_members").select("group_id,role").eq("user_id", uid).execute()
        gids = [str(r["group_id"]) for r in (gm.data or [])]
        if not gids:
            return _with_pending_hint([], client)
        gr = (
            client.table("groups")
            .select("id,name,kind,created_at,settings")
            .in_("id", gids)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (gr.data or [])], client)
    except Exception as e:
        return {"result": _err(f"list_my_groups: {e!s}")}


@mcp.tool()
def create_group_invite(api_key: str, group_id: str, expires_in_days: int = 7) -> dict[str, Any]:
    """Generate an invite code for a group (share it out-of-band with the new member)."""
    try:
        client = _get_client(api_key)
        res = client.rpc(
            "fn_create_group_invite",
            {"p_group_id": group_id, "p_expires_in_days": expires_in_days},
        ).execute()
        return _with_pending_hint({"status": "success", "invite_code": getattr(res, "data", None)}, client)
    except Exception as e:
        return {"result": _err(f"fn_create_group_invite: {e!s}")}


@mcp.tool()
def redeem_group_invite(api_key: str, invite_code: str) -> dict[str, Any]:
    """Join a group using an invite code."""
    try:
        client = _get_client(api_key)
        res = client.rpc(
            "fn_redeem_group_invite",
            {"p_code": invite_code.strip().lower()},
        ).execute()
        gid = getattr(res, "data", None)
        return _with_pending_hint({"status": "success", "group_id": str(gid) if gid else None}, client)
    except Exception as e:
        return {"result": _err(f"redeem_group_invite: {e!s}")}


@mcp.tool()
def list_group_members(api_key: str, group_id: str) -> dict[str, Any]:
    """List members of a group with their roles."""
    try:
        client = _get_client(api_key)
        res = (
            client.table("group_members")
            .select("user_id,role,joined_at")
            .eq("group_id", group_id)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])], client)
    except Exception as e:
        return {"result": _err(f"list_group_members: {e!s}")}


# ---------------------------------------------------------------------------
# Group expense tools
# ---------------------------------------------------------------------------

@mcp.tool()
def add_group_expense(
    api_key: str,
    group_id: str,
    expense_date: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = "",
    payer_user_id: str | None = None,
) -> dict[str, Any]:
    """
    Add a shared expense split equally among all members (amount in INR).
    Status starts as pending — all other members must approve before it counts.
    payer_user_id defaults to the caller (the person who paid the bill).
    """
    try:
        client = _get_client(api_key)
        res = client.rpc("fn_add_group_expense", {
            "p_group_id": group_id,
            "p_expense_date": expense_date,
            "p_amount": amount,
            "p_category": category,
            "p_subcategory": subcategory or "",
            "p_note": note or "",
            "p_payer_id": payer_user_id or None,
        }).execute()
        tid = getattr(res, "data", None)
        return _with_pending_hint(
            {"status": "success", "transaction_id": str(tid) if tid else None}, client
        )
    except Exception as e:
        return {"result": _err(f"fn_add_group_expense: {e!s}")}


@mcp.tool()
def vote_on_transaction(api_key: str, transaction_id: str, vote: str) -> dict[str, Any]:
    """Vote approve or reject on a pending group expense. Cannot vote on your own submission."""
    try:
        client = _get_client(api_key)
        res = client.rpc(
            "fn_vote_on_transaction",
            {"p_transaction_id": transaction_id, "p_vote": vote.strip().lower()},
        ).execute()
        return _with_pending_hint({"status": "success", "vote_result": getattr(res, "data", None)}, client)
    except Exception as e:
        return {"result": _err(f"vote: {e!s}")}


@mcp.tool()
def approve_group_expense(api_key: str, transaction_id: str) -> dict[str, Any]:
    """Approve a pending group expense."""
    return vote_on_transaction(api_key, transaction_id, "approve")


@mcp.tool()
def reject_group_expense(api_key: str, transaction_id: str) -> dict[str, Any]:
    """Reject a pending group expense (finalises as rejected immediately)."""
    return vote_on_transaction(api_key, transaction_id, "reject")


@mcp.tool()
def list_pending_group_expenses(api_key: str, group_id: str) -> dict[str, Any]:
    """List all pending expenses for a group."""
    try:
        client = _get_client(api_key)
        res = (
            client.table("transactions")
            .select("id,submitted_by,payer_id,expense_date,amount,category,subcategory,note,status")
            .eq("group_id", group_id)
            .eq("status", "pending")
            .order("expense_date", desc=True)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])], client)
    except Exception as e:
        return {"result": _err(f"list_pending_group_expenses: {e!s}")}


@mcp.tool()
def list_my_pending_approvals(api_key: str) -> dict[str, Any]:
    """List all group expenses waiting for your approval."""
    try:
        client = _get_client(api_key)
        uid = jwt_subject(get_jwt_from_client(client))
        res = client.rpc("fn_get_pending_count", {"p_user_id": uid}).execute()
        if res.data:
            row = res.data[0]
            return _with_pending_hint(
                {"count": row.get("count", 0), "items": row.get("sample") or []}, client
            )
        return _with_pending_hint({"count": 0, "items": []}, client)
    except Exception as e:
        return {"result": _err(f"list_my_pending_approvals: {e!s}")}


@mcp.tool()
def list_group_transactions(
    api_key: str,
    group_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """List all transactions for a group, optionally filtered by date range."""
    try:
        client = _get_client(api_key)
        q = (
            client.table("transactions")
            .select(
                "id,submitted_by,payer_id,expense_date,amount,"
                "category,subcategory,note,status,created_at"
            )
            .eq("group_id", group_id)
        )
        if start_date:
            q = q.gte("expense_date", start_date)
        if end_date:
            q = q.lte("expense_date", end_date)
        res = q.order("expense_date", desc=True).execute()
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])], client)
    except Exception as e:
        return {"result": _err(f"list_group_transactions: {e!s}")}


# ---------------------------------------------------------------------------
# Balance & settlement tools
# ---------------------------------------------------------------------------

@mcp.tool()
def group_balances(api_key: str, group_id: str, include_settlements: bool = True) -> dict[str, Any]:
    """
    Per-member net balance in INR.
    Positive = others owe them. Negative = they owe others.
    """
    try:
        client = _get_client(api_key)
        if include_settlements:
            res = client.rpc(
                "fn_group_balances_with_settlements", {"p_group_id": group_id}
            ).execute()
            net = {str(r["user_id"]): float(r["net_balance"]) for r in (res.data or [])}
        else:
            res = (
                client.table("transactions")
                .select("id,payer_id,amount,transaction_splits(member_id,share_amount)")
                .eq("group_id", group_id)
                .eq("status", "approved")
                .execute()
            )
            net = accumulate_group_balances(res.data or [])
        return _with_pending_hint({"group_id": group_id, "net_by_user_id": net}, client)
    except Exception as e:
        return {"result": _err(f"group_balances: {e!s}")}


@mcp.tool()
def simplify_group_debts(api_key: str, group_id: str, include_settlements: bool = True) -> dict[str, Any]:
    """Suggest the minimum set of transfers (in INR) to fully settle the group."""
    try:
        client = _get_client(api_key)
        if include_settlements:
            res = client.rpc(
                "fn_group_balances_with_settlements", {"p_group_id": group_id}
            ).execute()
            net = {str(r["user_id"]): float(r["net_balance"]) for r in (res.data or [])}
        else:
            res = (
                client.table("transactions")
                .select("id,payer_id,amount,transaction_splits(member_id,share_amount)")
                .eq("group_id", group_id)
                .eq("status", "approved")
                .execute()
            )
            net = accumulate_group_balances(res.data or [])
        return _with_pending_hint({
            "group_id": group_id,
            "net_by_user_id": net,
            "suggested_transfers": simplify_debts(net),
        }, client)
    except Exception as e:
        return {"result": _err(f"simplify_group_debts: {e!s}")}


@mcp.tool()
def record_settlement(
    api_key: str,
    group_id: str,
    from_user_id: str,
    to_user_id: str,
    amount: float,
    payment_date: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """
    Record a real payment between members (UPI, PhonePe, GPay, cash, etc.).
    This reduces the outstanding balance. Call after the money has actually moved.

    Args:
        from_user_id: Who paid (debtor).
        to_user_id: Who received (creditor).
        amount: Amount in INR.
        note: e.g. "PhonePe", "GPay", "Cash".
    """
    try:
        client = _get_client(api_key)
        args: dict[str, Any] = {
            "p_group_id": group_id,
            "p_from_user_id": from_user_id,
            "p_to_user_id": to_user_id,
            "p_amount": amount,
            "p_note": note or "",
        }
        if payment_date:
            args["p_payment_date"] = payment_date
        res = client.rpc("fn_record_settlement", args).execute()
        sid = getattr(res, "data", None)
        return _with_pending_hint({
            "status": "success",
            "settlement_id": str(sid) if sid else None,
            "message": f"Recorded: {from_user_id} → {to_user_id} ₹{amount}",
        }, client)
    except Exception as e:
        return {"result": _err(f"record_settlement: {e!s}")}


@mcp.tool()
def list_group_settlements(
    api_key: str,
    group_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """List recorded settlement payments for a group."""
    try:
        client = _get_client(api_key)
        q = (
            client.table("settlement_payments")
            .select("id,from_user_id,to_user_id,amount,payment_date,note,recorded_by,created_at")
            .eq("group_id", group_id)
        )
        if start_date:
            q = q.gte("payment_date", start_date)
        if end_date:
            q = q.lte("payment_date", end_date)
        res = q.order("payment_date", desc=True).execute()
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])], client)
    except Exception as e:
        return {"result": _err(f"list_group_settlements: {e!s}")}


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------

@mcp.resource("expense:///categories", mime_type="application/json")
def categories() -> str:
    """Available expense categories."""
    try:
        return Path(CATEGORIES_PATH).read_text(encoding="utf-8")
    except FileNotFoundError:
        return json.dumps(_DEFAULT_CATEGORIES, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Could not load categories: {e!s}"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("MCP_PORT", "8000")))
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
