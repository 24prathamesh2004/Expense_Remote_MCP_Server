"""
FastMCP expense + group finance (Supabase).

Env: SUPABASE_URL, SUPABASE_ANON_KEY
     SUPABASE_ACCESS_TOKEN  (local dev only — production uses API keys via DB)

SQL migrations (run in order):
  002_collaborative_finance.sql
  005_settlement_recording.sql
  007_self_service_registration.sql
  004_pending_approvals_optimization.sql
  006_security_audit.sql
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
from expense_mcp.supabase_client import get_anon_client, get_user_client, require_access_token

load_dotenv()

mcp = FastMCP("Expense (Supabase + Groups)")

_ROOT = Path(__file__).resolve().parent.parent.parent
CATEGORIES_PATH = Path(os.environ.get("EXPENSE_CATEGORIES_PATH", str(_ROOT / "categories.json")))

_DEFAULT_CATEGORIES = {
    "categories": [
        "Food & Dining",
        "Groceries",
        "Transportation",
        "Fuel & Vehicle",
        "Shopping",
        "Entertainment",
        "Bills & Utilities",
        "Mobile & Internet",
        "Healthcare",
        "Travel",
        "Education",
        "Rent",
        "EMI & Loans",
        "Investments",
        "Donations",
        "Personal Care",
        "Household",
        "Business",
        "Other",
    ]
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _err(message: str) -> dict[str, str]:
    return {"status": "error", "message": message}


def _jsonable_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert Decimal/date values to JSON-serialisable types."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _with_pending_hint(payload: Any) -> dict[str, Any]:
    """Wrap payload and attach pending-approvals summary (single RPC via materialized view)."""
    try:
        uid = jwt_subject(require_access_token())
        client = get_user_client()
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
# Auth / Registration tools
# ---------------------------------------------------------------------------

@mcp.tool()
def whoami() -> dict[str, Any]:
    """Return the current user's id (JWT sub). Useful to confirm authentication is working."""
    try:
        sub = jwt_subject(require_access_token())
        return {"status": "success", "user_id": sub}
    except Exception as e:
        return _err(str(e))


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
        res = get_anon_client().rpc(
            "fn_register_user",
            {"p_email": email, "p_password": password, "p_full_name": full_name or ""},
        ).execute()
        data = getattr(res, "data", {})
        return data if isinstance(data, dict) else _err("Unexpected response from registration")
    except Exception as e:
        return _err(f"Registration failed: {e!s}")


@mcp.tool()
def login_get_api_key(email: str, password: str) -> dict[str, Any]:
    """
    🔑 Get your API key (for existing users who lost theirs or set up a new device).

    Args:
        email: Your registered email.
        password: Your password.
    """
    try:
        res = get_anon_client().rpc(
            "fn_login_get_key",
            {"p_email": email, "p_password": password},
        ).execute()
        data = getattr(res, "data", {})
        return data if isinstance(data, dict) else _err("Unexpected response from login")
    except Exception as e:
        return _err(f"Login failed: {e!s}")


@mcp.tool()
def revoke_my_api_key(api_key: str) -> dict[str, Any]:
    """
    🚫 Revoke a compromised API key. Login again afterwards to get a new one.

    Args:
        api_key: The key to revoke (starts with exp_).
    """
    try:
        res = get_user_client().rpc("fn_revoke_api_key", {"p_api_key": api_key}).execute()
        data = getattr(res, "data", {})
        return data if isinstance(data, dict) else _err("Unexpected response")
    except Exception as e:
        return _err(f"Revoke failed: {e!s}")


# ---------------------------------------------------------------------------
# Personal expense tools
# ---------------------------------------------------------------------------

@mcp.tool()
def add_expense(
    date: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Add a personal expense (no group). Amount in INR."""
    try:
        uid = jwt_subject(require_access_token())
        res = get_user_client().table("transactions").insert({
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
            return _with_pending_hint(_err("Insert returned no data."))
        return _with_pending_hint({
            "status": "success",
            "id": str(res.data[0].get("id", "")),
            "message": "Expense added successfully",
        })
    except Exception as e:
        return _with_pending_hint(_err(f"Database error: {e!s}"))


@mcp.tool()
def list_expenses(start_date: str, end_date: str) -> dict[str, Any]:
    """List personal expenses in an inclusive date range (YYYY-MM-DD)."""
    try:
        res = (
            get_user_client().table("transactions")
            .select("id,expense_date,amount,category,subcategory,note,created_at,status")
            .is_("group_id", None)
            .gte("expense_date", start_date)
            .lte("expense_date", end_date)
            .order("expense_date", desc=True)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])])
    except Exception as e:
        return _with_pending_hint(_err(f"Error listing expenses: {e!s}"))


@mcp.tool()
def summarize(start_date: str, end_date: str, category: str | None = None) -> dict[str, Any]:
    """Personal spending totals by category for a date range."""
    try:
        q = (
            get_user_client().table("transactions")
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
        return _with_pending_hint(out)
    except Exception as e:
        return _with_pending_hint(_err(f"Error summarizing expenses: {e!s}"))


# ---------------------------------------------------------------------------
# Group management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def create_group(name: str, kind: str = "trip") -> dict[str, Any]:
    """Create a group and add caller as owner. kind: trip | family | team | personal_mirror."""
    try:
        res = get_user_client().rpc("fn_create_group", {"p_name": name, "p_kind": kind}).execute()
        gid = getattr(res, "data", None)
        return _with_pending_hint({"status": "success", "group_id": str(gid) if gid else None})
    except Exception as e:
        return _with_pending_hint(_err(f"fn_create_group: {e!s}"))


@mcp.tool()
def list_my_groups() -> dict[str, Any]:
    """List all groups the signed-in user belongs to."""
    try:
        uid = jwt_subject(require_access_token())
        client = get_user_client()
        gm = client.table("group_members").select("group_id,role").eq("user_id", uid).execute()
        gids = [str(r["group_id"]) for r in (gm.data or [])]
        if not gids:
            return _with_pending_hint([])
        gr = client.table("groups").select("id,name,kind,created_at,settings").in_("id", gids).execute()
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (gr.data or [])])
    except Exception as e:
        return _with_pending_hint(_err(f"list_my_groups: {e!s}"))


@mcp.tool()
def create_group_invite(group_id: str, expires_in_days: int = 7) -> dict[str, Any]:
    """Generate an invite code for a group (share it out-of-band with the new member)."""
    try:
        res = get_user_client().rpc(
            "fn_create_group_invite",
            {"p_group_id": group_id, "p_expires_in_days": expires_in_days},
        ).execute()
        return _with_pending_hint({"status": "success", "invite_code": getattr(res, "data", None)})
    except Exception as e:
        return _with_pending_hint(_err(f"fn_create_group_invite: {e!s}"))


@mcp.tool()
def redeem_group_invite(invite_code: str) -> dict[str, Any]:
    """Join a group using an invite code."""
    try:
        res = get_user_client().rpc(
            "fn_redeem_group_invite",
            {"p_code": invite_code.strip().lower()},
        ).execute()
        gid = getattr(res, "data", None)
        return _with_pending_hint({"status": "success", "group_id": str(gid) if gid else None})
    except Exception as e:
        return _with_pending_hint(_err(f"redeem: {e!s}"))


@mcp.tool()
def list_group_members(group_id: str) -> dict[str, Any]:
    """List members of a group with their roles."""
    try:
        res = (
            get_user_client().table("group_members")
            .select("user_id,role,joined_at")
            .eq("group_id", group_id)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])])
    except Exception as e:
        return _with_pending_hint(_err(f"list_group_members: {e!s}"))


# ---------------------------------------------------------------------------
# Group expense tools
# ---------------------------------------------------------------------------

@mcp.tool()
def add_group_expense(
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
        args: dict[str, Any] = {
            "p_group_id": group_id,
            "p_expense_date": expense_date,
            "p_amount": amount,
            "p_category": category,
            "p_subcategory": subcategory or "",
            "p_note": note or "",
            "p_payer_id": payer_user_id or None,
        }
        res = get_user_client().rpc("fn_add_group_expense", args).execute()
        tid = getattr(res, "data", None)
        return _with_pending_hint({"status": "success", "transaction_id": str(tid) if tid else None})
    except Exception as e:
        return _with_pending_hint(_err(f"fn_add_group_expense: {e!s}"))


@mcp.tool()
def vote_on_transaction(transaction_id: str, vote: str) -> dict[str, Any]:
    """Vote approve or reject on a pending group expense. You cannot vote on your own submission."""
    try:
        res = get_user_client().rpc(
            "fn_vote_on_transaction",
            {"p_transaction_id": transaction_id, "p_vote": vote.strip().lower()},
        ).execute()
        return _with_pending_hint({"status": "success", "vote_result": getattr(res, "data", None)})
    except Exception as e:
        return _with_pending_hint(_err(f"vote: {e!s}"))


@mcp.tool()
def approve_group_expense(transaction_id: str) -> dict[str, Any]:
    """Approve a pending group expense."""
    return vote_on_transaction(transaction_id, "approve")


@mcp.tool()
def reject_group_expense(transaction_id: str) -> dict[str, Any]:
    """Reject a pending group expense (finalises as rejected immediately)."""
    return vote_on_transaction(transaction_id, "reject")


@mcp.tool()
def list_pending_group_expenses(group_id: str) -> dict[str, Any]:
    """List all pending expenses for a group."""
    try:
        res = (
            get_user_client().table("transactions")
            .select("id,submitted_by,payer_id,expense_date,amount,category,subcategory,note,status")
            .eq("group_id", group_id)
            .eq("status", "pending")
            .order("expense_date", desc=True)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])])
    except Exception as e:
        return _with_pending_hint(_err(f"pending list: {e!s}"))


@mcp.tool()
def list_my_pending_approvals() -> dict[str, Any]:
    """List all group expenses waiting for your approval (excludes ones you already approved)."""
    try:
        uid = jwt_subject(require_access_token())
        res = get_user_client().rpc("fn_get_pending_count", {"p_user_id": uid}).execute()
        if res.data:
            row = res.data[0]
            return _with_pending_hint({
                "count": row.get("count", 0),
                "items": row.get("sample") or [],
            })
        return _with_pending_hint({"count": 0, "items": []})
    except Exception as e:
        return _with_pending_hint(_err(f"pending approvals: {e!s}"))


@mcp.tool()
def list_group_transactions(
    group_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """List all transactions for a group, optionally filtered by date range."""
    try:
        q = (
            get_user_client().table("transactions")
            .select("id,submitted_by,payer_id,expense_date,amount,category,subcategory,note,status,created_at")
            .eq("group_id", group_id)
        )
        if start_date:
            q = q.gte("expense_date", start_date)
        if end_date:
            q = q.lte("expense_date", end_date)
        res = q.order("expense_date", desc=True).execute()
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])])
    except Exception as e:
        return _with_pending_hint(_err(f"list_group_transactions: {e!s}"))


# ---------------------------------------------------------------------------
# Balance & settlement tools
# ---------------------------------------------------------------------------

@mcp.tool()
def group_balances(group_id: str, include_settlements: bool = True) -> dict[str, Any]:
    """
    Per-member net balance in INR (positive = others owe them, negative = they owe others).
    Set include_settlements=False to see balances from expenses only.
    """
    try:
        client = get_user_client()
        if include_settlements:
            res = client.rpc("fn_group_balances_with_settlements", {"p_group_id": group_id}).execute()
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
        return _with_pending_hint({"group_id": group_id, "net_by_user_id": net})
    except Exception as e:
        return _with_pending_hint(_err(f"group_balances: {e!s}"))


@mcp.tool()
def simplify_group_debts(group_id: str, include_settlements: bool = True) -> dict[str, Any]:
    """
    Suggest the minimum set of transfers (in INR) to fully settle the group.
    Set include_settlements=False to ignore already-recorded payments.
    """
    try:
        client = get_user_client()
        if include_settlements:
            res = client.rpc("fn_group_balances_with_settlements", {"p_group_id": group_id}).execute()
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
        })
    except Exception as e:
        return _with_pending_hint(_err(f"simplify_group_debts: {e!s}"))


@mcp.tool()
def record_settlement(
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
        args: dict[str, Any] = {
            "p_group_id": group_id,
            "p_from_user_id": from_user_id,
            "p_to_user_id": to_user_id,
            "p_amount": amount,
            "p_note": note or "",
        }
        if payment_date:
            args["p_payment_date"] = payment_date
        res = get_user_client().rpc("fn_record_settlement", args).execute()
        sid = getattr(res, "data", None)
        return _with_pending_hint({
            "status": "success",
            "settlement_id": str(sid) if sid else None,
            "message": f"Recorded: {from_user_id} → {to_user_id} ₹{amount}",
        })
    except Exception as e:
        return _with_pending_hint(_err(f"record_settlement: {e!s}"))


@mcp.tool()
def list_group_settlements(
    group_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """List recorded settlement payments for a group."""
    try:
        q = (
            get_user_client().table("settlement_payments")
            .select("id,from_user_id,to_user_id,amount,payment_date,note,recorded_by,created_at")
            .eq("group_id", group_id)
        )
        if start_date:
            q = q.gte("payment_date", start_date)
        if end_date:
            q = q.lte("payment_date", end_date)
        res = q.order("payment_date", desc=True).execute()
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])])
    except Exception as e:
        return _with_pending_hint(_err(f"list_group_settlements: {e!s}"))


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
    port = int(os.environ.get("MCP_PORT", "8000"))
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
