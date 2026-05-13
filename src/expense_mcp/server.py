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
  009_fix_invite_and_rls.sql
  004_pending_approvals_optimization.sql  (optional)
  006_security_audit.sql                  (optional)
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
    AuthedClient,
    get_anon_client,
    get_client_for_api_key,
    get_client_for_env_token,
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
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()}


def _get_ac(api_key: str) -> AuthedClient:
    """Return an AuthedClient. Uses api_key in production, env token in local dev."""
    if api_key:
        return get_client_for_api_key(api_key)
    return get_client_for_env_token()


def _with_pending_hint(payload: Any, ac: AuthedClient) -> dict[str, Any]:
    """Wrap payload and attach pending-approvals summary using the already-authed client."""
    try:
        uid = jwt_subject(ac.access_token)
        base: dict[str, Any] = {"result": payload}
        res = ac.client.rpc("fn_get_pending_count", {"p_user_id": uid}).execute()
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
            # Store fresh refresh token so future calls work
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


# @mcp.tool()
# def whoami(api_key: str = "") -> dict[str, Any]:
#     """Return the current user's id. Confirms authentication is working."""
#     try:
#         ac = _get_ac(api_key)
#         return {"status": "success", "user_id": jwt_subject(ac.access_token)}
#     except Exception as e:
#         return _err(str(e))


# @mcp.tool()
# def revoke_my_api_key(api_key: str) -> dict[str, Any]:
#     """🚫 Revoke a compromised API key. Call login_get_api_key() afterwards to get a new one."""
#     try:
#         ac = _get_ac(api_key)
#         res = ac.client.rpc("fn_revoke_api_key", {"p_api_key": api_key}).execute()
#         data = getattr(res, "data", {})
#         return data if isinstance(data, dict) else _err("Unexpected response")
#     except Exception as e:
#         return _err(f"Revoke failed: {e!s}")


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
        ac = _get_ac(api_key)
        uid = jwt_subject(ac.access_token)
        res = ac.client.table("transactions").insert({
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
            return _with_pending_hint(_err("Insert returned no data."), ac)
        return _with_pending_hint({
            "status": "success",
            "id": str(res.data[0].get("id", "")),
            "message": "Expense added successfully",
        }, ac)
    except Exception as e:
        return {"result": _err(f"Database error: {e!s}")}


@mcp.tool()
def list_expenses(api_key: str, start_date: str, end_date: str) -> dict[str, Any]:
    """List personal expenses in an inclusive date range (YYYY-MM-DD)."""
    try:
        ac = _get_ac(api_key)
        res = (
            ac.client.table("transactions")
            .select("id,expense_date,amount,category,subcategory,note,created_at,status")
            .is_("group_id", None)
            .gte("expense_date", start_date)
            .lte("expense_date", end_date)
            .order("expense_date", desc=True)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])], ac)
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
        ac = _get_ac(api_key)
        q = (
            ac.client.table("transactions")
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
        return _with_pending_hint(out, ac)
    except Exception as e:
        return {"result": _err(f"Error summarizing: {e!s}")}


@mcp.tool()
def delete_expense(api_key: str, expense_id: str) -> dict[str, Any]:
    """Delete a personal expense by its ID. Only works on your own expenses."""
    try:
        ac = _get_ac(api_key)
        uid = jwt_subject(ac.access_token)
        # Verify it exists and belongs to the user before deleting
        check = (
            ac.client.table("transactions")
            .select("id")
            .eq("id", expense_id)
            .eq("submitted_by", uid)
            .is_("group_id", None)
            .execute()
        )
        if not check.data:
            return _err("Expense not found or you don't have permission to delete it.")
        ac.client.table("transactions").delete().eq("id", expense_id).execute()
        return _with_pending_hint({
            "status": "success",
            "message": f"Expense {expense_id} deleted.",
        }, ac)
    except Exception as e:
        return {"result": _err(f"Delete failed: {e!s}")}


@mcp.tool()
def edit_expense(
    api_key: str,
    expense_id: str,
    date: str | None = None,
    amount: float | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """
    Edit a personal expense. Only pass the fields you want to change.

    Args:
        expense_id: ID of the expense to edit (from list_expenses).
        date: New date (YYYY-MM-DD), optional.
        amount: New amount in INR, optional.
        category: New category, optional.
        subcategory: New subcategory, optional.
        note: New note, optional.
    """
    try:
        ac = _get_ac(api_key)
        uid = jwt_subject(ac.access_token)

        # Verify ownership
        check = (
            ac.client.table("transactions")
            .select("id,expense_date,amount,category,subcategory,note")
            .eq("id", expense_id)
            .eq("submitted_by", uid)
            .is_("group_id", None)
            .execute()
        )
        if not check.data:
            return _err("Expense not found or you don't have permission to edit it.")

        # Build update payload with only provided fields
        updates: dict[str, Any] = {}
        if date is not None:
            updates["expense_date"] = date
        if amount is not None:
            updates["amount"] = amount
        if category is not None:
            updates["category"] = category
        if subcategory is not None:
            updates["subcategory"] = subcategory
        if note is not None:
            updates["note"] = note

        if not updates:
            return _err("No fields provided to update.")

        res = (
            ac.client.table("transactions")
            .update(updates)
            .eq("id", expense_id)
            .execute()
        )
        if not res.data:
            return _err("Update failed — no rows affected.")

        return _with_pending_hint({
            "status": "success",
            "message": "Expense updated successfully.",
            "updated": _jsonable_row(dict(res.data[0])),
        }, ac)
    except Exception as e:
        return {"result": _err(f"Edit failed: {e!s}")}


@mcp.tool()
def monthly_report(api_key: str, month: int, year: int) -> dict[str, Any]:
    """
    Full spending report for a given month.

    Returns total spent, category breakdown, biggest single expense,
    daily totals, busiest spending day, and week-wise pattern.

    Args:
        month: Month number (1-12).
        year: Four-digit year e.g. 2026.
    """
    try:
        import calendar
        ac = _get_ac(api_key)

        # Build date range for the month
        last_day = calendar.monthrange(year, month)[1]
        start = f"{year}-{month:02d}-01"
        end = f"{year}-{month:02d}-{last_day:02d}"

        rows = (
            ac.client.table("transactions")
            .select("id,expense_date,amount,category,subcategory,note")
            .is_("group_id", None)
            .gte("expense_date", start)
            .lte("expense_date", end)
            .order("expense_date", desc=False)
            .execute()
        ).data or []

        if not rows:
            return _with_pending_hint({
                "month": f"{calendar.month_name[month]} {year}",
                "message": "No expenses recorded this month.",
                "total_spent": 0,
            }, ac)

        total = 0.0
        category_buckets: dict[str, float] = {}
        daily_totals: dict[str, float] = {}
        biggest = {"amount": 0.0, "category": "", "note": "", "date": ""}
        weekday_totals: dict[str, float] = {
            "Monday": 0.0, "Tuesday": 0.0, "Wednesday": 0.0,
            "Thursday": 0.0, "Friday": 0.0, "Saturday": 0.0, "Sunday": 0.0,
        }

        for r in rows:
            amt = float(r.get("amount") or 0)
            cat = r.get("category") or "Other"
            date_str = str(r.get("expense_date", ""))
            note = r.get("note") or ""

            total += amt

            # Category breakdown
            category_buckets[cat] = category_buckets.get(cat, 0.0) + amt

            # Daily totals
            daily_totals[date_str] = daily_totals.get(date_str, 0.0) + amt

            # Biggest single expense
            if amt > biggest["amount"]:
                biggest = {"amount": amt, "category": cat, "note": note, "date": date_str}

            # Weekday pattern
            try:
                from datetime import date as dt_date
                y, m, d = map(int, date_str.split("-"))
                weekday = dt_date(y, m, d).strftime("%A")
                weekday_totals[weekday] = weekday_totals.get(weekday, 0.0) + amt
            except Exception:
                pass

        # Top categories sorted by spend
        top_categories = [
            {
                "category": cat,
                "total": round(amt, 2),
                "percentage": round(amt / total * 100, 1),
            }
            for cat, amt in sorted(category_buckets.items(), key=lambda x: -x[1])
        ]

        # Busiest day
        busiest_day = max(daily_totals, key=lambda d: daily_totals[d]) if daily_totals else None

        # Weekend vs weekday split
        weekend = round(weekday_totals["Saturday"] + weekday_totals["Sunday"], 2)
        weekday = round(total - weekend, 2)

        # Daily totals list (for day-wise trend)
        daily_list = [
            {"date": d, "total": round(v, 2)}
            for d, v in sorted(daily_totals.items())
        ]

        return _with_pending_hint({
            "month": f"{calendar.month_name[month]} {year}",
            "total_spent": round(total, 2),
            "transaction_count": len(rows),
            "top_categories": top_categories,
            "biggest_expense": {
                "amount": round(biggest["amount"], 2),
                "category": biggest["category"],
                "note": biggest["note"],
                "date": biggest["date"],
            },
            "busiest_day": {
                "date": busiest_day,
                "total": round(daily_totals.get(busiest_day, 0), 2) if busiest_day else 0,
            },
            "weekday_vs_weekend": {
                "weekdays_total": weekday,
                "weekends_total": weekend,
                "weekend_percentage": round(weekend / total * 100, 1) if total else 0,
            },
            "daily_totals": daily_list,
        }, ac)
    except Exception as e:
        return {"result": _err(f"monthly_report: {e!s}")}


# ---------------------------------------------------------------------------
# Group management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def create_group(api_key: str, name: str, kind: str = "trip") -> dict[str, Any]:
    """Create a group and add caller as owner. kind: trip | family | team | personal_mirror."""
    try:
        ac = _get_ac(api_key)
        res = ac.client.rpc("fn_create_group", {"p_name": name, "p_kind": kind}).execute()
        gid = getattr(res, "data", None)
        return _with_pending_hint({"status": "success", "group_id": str(gid) if gid else None}, ac)
    except Exception as e:
        return {"result": _err(f"fn_create_group: {e!s}")}


@mcp.tool()
def list_my_groups(api_key: str) -> dict[str, Any]:
    """List all groups the signed-in user belongs to."""
    try:
        ac = _get_ac(api_key)
        uid = jwt_subject(ac.access_token)
        gm = ac.client.table("group_members").select("group_id,role").eq("user_id", uid).execute()
        gids = [str(r["group_id"]) for r in (gm.data or [])]
        if not gids:
            return _with_pending_hint([], ac)
        gr = (
            ac.client.table("groups")
            .select("id,name,kind,created_at,settings")
            .in_("id", gids)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (gr.data or [])], ac)
    except Exception as e:
        return {"result": _err(f"list_my_groups: {e!s}")}


@mcp.tool()
def create_group_invite(api_key: str, group_id: str, expires_in_days: int = 7) -> dict[str, Any]:
    """Generate an invite code for a group (share it out-of-band with the new member)."""
    try:
        ac = _get_ac(api_key)
        res = ac.client.rpc(
            "fn_create_group_invite",
            {"p_group_id": group_id, "p_expires_in_days": expires_in_days},
        ).execute()
        return _with_pending_hint({"status": "success", "invite_code": getattr(res, "data", None)}, ac)
    except Exception as e:
        return {"result": _err(f"fn_create_group_invite: {e!s}")}


@mcp.tool()
def redeem_group_invite(api_key: str, invite_code: str) -> dict[str, Any]:
    """Join a group using an invite code."""
    try:
        ac = _get_ac(api_key)
        res = ac.client.rpc(
            "fn_redeem_group_invite",
            {"p_code": invite_code.strip().lower()},
        ).execute()
        gid = getattr(res, "data", None)
        return _with_pending_hint({"status": "success", "group_id": str(gid) if gid else None}, ac)
    except Exception as e:
        return {"result": _err(f"redeem_group_invite: {e!s}")}


@mcp.tool()
def list_group_members(api_key: str, group_id: str) -> dict[str, Any]:
    """List members of a group with their roles."""
    try:
        ac = _get_ac(api_key)
        res = (
            ac.client.table("group_members")
            .select("user_id,role,joined_at")
            .eq("group_id", group_id)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])], ac)
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
        ac = _get_ac(api_key)
        res = ac.client.rpc("fn_add_group_expense", {
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
            {"status": "success", "transaction_id": str(tid) if tid else None}, ac
        )
    except Exception as e:
        return {"result": _err(f"fn_add_group_expense: {e!s}")}


def vote_on_transaction(api_key: str, transaction_id: str, vote: str) -> dict[str, Any]:
    """Vote approve or reject on a pending group expense. Cannot vote on your own submission."""
    try:
        ac = _get_ac(api_key)
        res = ac.client.rpc(
            "fn_vote_on_transaction",
            {"p_transaction_id": transaction_id, "p_vote": vote.strip().lower()},
        ).execute()
        return _with_pending_hint({"status": "success", "vote_result": getattr(res, "data", None)}, ac)
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
        ac = _get_ac(api_key)
        res = (
            ac.client.table("transactions")
            .select("id,submitted_by,payer_id,expense_date,amount,category,subcategory,note,status")
            .eq("group_id", group_id)
            .eq("status", "pending")
            .order("expense_date", desc=True)
            .execute()
        )
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])], ac)
    except Exception as e:
        return {"result": _err(f"list_pending_group_expenses: {e!s}")}


@mcp.tool()
def list_my_pending_approvals(api_key: str) -> dict[str, Any]:
    """List all group expenses waiting for your approval."""
    try:
        ac = _get_ac(api_key)
        uid = jwt_subject(ac.access_token)
        res = ac.client.rpc("fn_get_pending_count", {"p_user_id": uid}).execute()
        if res.data:
            row = res.data[0]
            return _with_pending_hint(
                {"count": row.get("count", 0), "items": row.get("sample") or []}, ac
            )
        return _with_pending_hint({"count": 0, "items": []}, ac)
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
        ac = _get_ac(api_key)
        q = (
            ac.client.table("transactions")
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
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])], ac)
    except Exception as e:
        return {"result": _err(f"list_group_transactions: {e!s}")}


@mcp.tool()
def delete_group_expense(api_key: str, transaction_id: str) -> dict[str, Any]:
    """
    Delete a pending group expense. Only the person who submitted it can delete it,
    and only while it is still pending (not yet approved or rejected).
    """
    try:
        ac = _get_ac(api_key)
        res = ac.client.rpc(
            "fn_delete_group_expense", {"p_transaction_id": transaction_id}
        ).execute()
        data = getattr(res, "data", {})
        return _with_pending_hint(data if isinstance(data, dict) else _err("Unexpected response"), ac)
    except Exception as e:
        return {"result": _err(f"delete_group_expense: {e!s}")}


@mcp.tool()
def group_summary(
    api_key: str,
    group_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """
    Spending breakdown for a group by category (approved expenses only).
    Optionally filter by date range. Great for trip/event summaries.

    Example: "What did we spend on food during the Goa trip?"
    """
    try:
        ac = _get_ac(api_key)
        q = (
            ac.client.table("transactions")
            .select("category,amount,payer_id,expense_date")
            .eq("group_id", group_id)
            .eq("status", "approved")
        )
        if start_date:
            q = q.gte("expense_date", start_date)
        if end_date:
            q = q.lte("expense_date", end_date)
        rows = q.execute().data or []

        buckets: dict[str, dict[str, Any]] = {}
        total = 0.0
        for r in rows:
            c = r.get("category") or "Other"
            amt = float(r.get("amount") or 0)
            buckets.setdefault(c, {"total_amount": 0.0, "count": 0})
            buckets[c]["total_amount"] += amt
            buckets[c]["count"] += 1
            total += amt

        breakdown = [
            {
                "category": c,
                "total_amount": round(v["total_amount"], 2),
                "count": v["count"],
                "percentage": round(v["total_amount"] / total * 100, 1) if total else 0,
            }
            for c, v in sorted(buckets.items(), key=lambda x: -x[1]["total_amount"])
        ]

        return _with_pending_hint({
            "group_id": group_id,
            "total_spent": round(total, 2),
            "transaction_count": len(rows),
            "breakdown_by_category": breakdown,
        }, ac)
    except Exception as e:
        return {"result": _err(f"group_summary: {e!s}")}


@mcp.tool()
def leave_group(api_key: str, group_id: str) -> dict[str, Any]:
    """
    Leave a group. You will lose access to all group data after leaving.
    Note: Group owners cannot leave — transfer ownership or delete the group first.
    """
    try:
        ac = _get_ac(api_key)
        res = ac.client.rpc("fn_leave_group", {"p_group_id": group_id}).execute()
        data = getattr(res, "data", {})
        return data if isinstance(data, dict) else _err("Unexpected response")
    except Exception as e:
        return {"result": _err(f"leave_group: {e!s}")}


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
        ac = _get_ac(api_key)
        if include_settlements:
            res = ac.client.rpc(
                "fn_group_balances_with_settlements", {"p_group_id": group_id}
            ).execute()
            net = {str(r["user_id"]): float(r["net_balance"]) for r in (res.data or [])}
        else:
            res = (
                ac.client.table("transactions")
                .select("id,payer_id,amount,transaction_splits(member_id,share_amount)")
                .eq("group_id", group_id)
                .eq("status", "approved")
                .execute()
            )
            net = accumulate_group_balances(res.data or [])
        return _with_pending_hint({"group_id": group_id, "net_by_user_id": net}, ac)
    except Exception as e:
        return {"result": _err(f"group_balances: {e!s}")}


@mcp.tool()
def simplify_group_debts(api_key: str, group_id: str, include_settlements: bool = True) -> dict[str, Any]:
    """Suggest the minimum set of transfers (in INR) to fully settle the group."""
    try:
        ac = _get_ac(api_key)
        if include_settlements:
            res = ac.client.rpc(
                "fn_group_balances_with_settlements", {"p_group_id": group_id}
            ).execute()
            net = {str(r["user_id"]): float(r["net_balance"]) for r in (res.data or [])}
        else:
            res = (
                ac.client.table("transactions")
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
        }, ac)
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
    """
    try:
        ac = _get_ac(api_key)
        args: dict[str, Any] = {
            "p_group_id": group_id,
            "p_from_user_id": from_user_id,
            "p_to_user_id": to_user_id,
            "p_amount": amount,
            "p_note": note or "",
        }
        if payment_date:
            args["p_payment_date"] = payment_date
        res = ac.client.rpc("fn_record_settlement", args).execute()
        sid = getattr(res, "data", None)
        return _with_pending_hint({
            "status": "success",
            "settlement_id": str(sid) if sid else None,
            "message": f"Recorded: {from_user_id} → {to_user_id} ₹{amount}",
        }, ac)
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
        ac = _get_ac(api_key)
        q = (
            ac.client.table("settlement_payments")
            .select("id,from_user_id,to_user_id,amount,payment_date,note,recorded_by,created_at")
            .eq("group_id", group_id)
        )
        if start_date:
            q = q.gte("payment_date", start_date)
        if end_date:
            q = q.lte("payment_date", end_date)
        res = q.order("payment_date", desc=True).execute()
        return _with_pending_hint([_jsonable_row(dict(r)) for r in (res.data or [])], ac)
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
