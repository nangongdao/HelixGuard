"""Signed submitter tokens for the embeddable submission portal (Phase 23.1).

The portal runs on the submitter's browser and must not hold an API key.
Instead, the embedding page obtains a short-lived signed token from the
tenant's backend (or a trusted issuer) that binds the request to a tenant
and optionally a submitter reference. Every portal endpoint verifies the
token with an HMAC-SHA256 signature and a replay window on the timestamp.

Token format: ``base64url(payload).signature`` where payload is
``{"tenant_id", "submitter_ref", "review_case_id", "iat", "exp"}`` and the
signature is HMAC-SHA256 of ``base64url(payload)`` using the shared secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any


class WidgetTokenError(ValueError):
    """Raised when a widget token is missing, expired, or forged."""


@dataclass(frozen=True)
class WidgetToken:
    tenant_id: str
    submitter_ref: str | None
    review_case_id: str | None
    iat: int
    exp: int


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def sign_token(
    *,
    secret: str,
    tenant_id: str,
    submitter_ref: str | None = None,
    review_case_id: str | None = None,
    ttl_seconds: int = 3600,
    now: int | None = None,
) -> str:
    """Issue a signed widget token for a tenant."""
    current = int(now if now is not None else time.time())
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "iat": current,
        "exp": current + ttl_seconds,
    }
    if submitter_ref:
        payload["submitter_ref"] = submitter_ref
    if review_case_id:
        payload["review_case_id"] = review_case_id
    encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def verify_token(
    *,
    secret: str,
    token: str,
    max_skew_seconds: int = 300,
    now: int | None = None,
) -> WidgetToken:
    """Verify a widget token, returning the bound tenant/submitter.

    Raises :class:`WidgetTokenError` on malformed, expired, or forged
    tokens, and rejects tokens whose issue time is in the future beyond
    ``max_skew_seconds`` (clock-skew / replay guard).
    """
    if not token or "." not in token:
        raise WidgetTokenError("Invalid widget token")
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WidgetTokenError("Invalid widget token signature")
    try:
        payload = json.loads(_b64decode(encoded))
    except (ValueError, TypeError) as exc:
        raise WidgetTokenError("Invalid widget token payload") from exc
    tenant_id = payload.get("tenant_id")
    exp = payload.get("exp")
    iat = payload.get("iat")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise WidgetTokenError("Widget token missing tenant_id")
    if not isinstance(exp, int) or not isinstance(iat, int):
        raise WidgetTokenError("Widget token missing timestamps")
    current = int(now if now is not None else time.time())
    if current > exp:
        raise WidgetTokenError("Widget token expired")
    if iat > current + max_skew_seconds:
        raise WidgetTokenError("Widget token issued in the future")
    submitter_ref = payload.get("submitter_ref")
    review_case_id = payload.get("review_case_id")
    return WidgetToken(
        tenant_id=tenant_id,
        submitter_ref=str(submitter_ref) if submitter_ref else None,
        review_case_id=str(review_case_id) if review_case_id else None,
        iat=iat,
        exp=exp,
    )


__all__ = [
    "WidgetToken",
    "WidgetTokenError",
    "sign_token",
    "verify_token",
]
