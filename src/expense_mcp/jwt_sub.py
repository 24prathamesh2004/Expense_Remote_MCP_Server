"""Extract auth subject from JWT payload (opaque decode only; token already trusted via env)."""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode


def jwt_subject(access_token: str) -> str:
    parts = access_token.strip().split(".")
    if len(parts) != 3:
        raise ValueError("Not a JWT (expected three segments).")
    body = parts[1]
    padded = body + "=" * (-len(body) % 4)
    payload = json.loads(urlsafe_b64decode(padded))
    sub = payload.get("sub")
    if not sub:
        raise ValueError("JWT missing sub claim.")
    return str(sub)
