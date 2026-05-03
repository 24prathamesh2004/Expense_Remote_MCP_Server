"""Group balance and debt simplification (approved transactions only)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def tx_row_to_decimal(v: Any) -> float:
    if isinstance(v, Decimal):
        return float(v)
    if v is None:
        return 0.0
    return float(v)


def accumulate_group_balances(transactions_payload: list[dict[str, Any]]) -> dict[str, float]:
    """
    Net balance per member: payer gets +(amount - own_share); each member −own_share.
    Splits must cover all members; sums to ~0.
    """
    net: dict[str, float] = {}
    for t in transactions_payload:
        payer = str(t["payer_id"])
        amt = tx_row_to_decimal(t.get("amount"))
        splits_raw = t.get("transaction_splits")
        splits: list = splits_raw if isinstance(splits_raw, list) else []
        payer_share = 0.0
        for s in splits:
            mid = str(s["member_id"])
            sha = tx_row_to_decimal(s.get("share_amount"))
            net[mid] = net.get(mid, 0.0) - sha
            if mid == payer:
                payer_share = sha
        net[payer] = net.get(payer, 0.0) + amt - payer_share
    return net


def simplify_debts(net: dict[str, float], eps: float = 1e-4) -> list[dict[str, str | float]]:
    """
    Greedy minimum-cash-flow: match largest debtor with largest creditor.
    Returns list of {from_user, to_user, amount}.
    """
    deb: list[tuple[str, float]] = []
    cred: list[tuple[str, float]] = []
    for uid, b in net.items():
        if b < -eps:
            deb.append((uid, -b))
        elif b > eps:
            cred.append((uid, b))
    deb.sort(key=lambda x: x[1], reverse=True)
    cred.sort(key=lambda x: x[1], reverse=True)
    out: list[dict[str, str | float]] = []
    i, j = 0, 0
    while i < len(deb) and j < len(cred):
        du, da = deb[i]
        cu, ca = cred[j]
        pay = round(min(da, ca), 2)
        if pay > eps:
            out.append({"from_user": du, "to_user": cu, "amount": pay})
        nda = round(da - pay, 2)
        nca = round(ca - pay, 2)
        if nda <= eps:
            i += 1
        else:
            deb[i] = (du, nda)
        if nca <= eps:
            j += 1
        else:
            cred[j] = (cu, nca)
    return out
