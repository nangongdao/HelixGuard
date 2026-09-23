"""ROADMAP H02 (2.19.0): operator attachment upload receipt (expand phase).

The send receipt (v47) closes the "retried reply becomes two customer-visible
messages" hole. One step earlier in the same operator flow the same hole is
open for the file itself: the upload commits, the response is lost to a
network blip or a browser timeout, the workspace retries, and the bytes are
stored a second time. The duplicate then burns the tenant quota twice and is
bindable to the next message, so one attempt becomes two attachments.

``attachments.reviewer_idempotency_key`` carries the caller's
``Idempotency-Key`` for the upload attempt that produced the row, with a
partial unique index over ``(tenant_id, review_case_id, key)``. The index
mirrors ``idx_messages_operator_send_receipt`` (v47) and
``idx_messages_channel_dedup`` (v11) for the same reasons: the same key may
replay without a duplicate ever becoming reachable, NULL keys (the default,
and every legacy row) are excluded from the constraint, and the key is
namespaced per conversation so the same string elsewhere is a different
request.

Recording the receipt on the attachment row — rather than in the
``api_idempotency`` resource-key store (v37) — is deliberate and follows v47:
``attachments`` is already covered by the retention list and the PostgreSQL
RLS policy set, so the receipt inherits those guarantees for free.
``api_idempotency`` has no ``review_case_id`` and appears in none of them.

Expand-only: one additive nullable column plus one index.
"""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(48, "operator attachment upload receipt (ROADMAP H02)", phase="expand")
def migrate(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "attachments", "reviewer_idempotency_key", "TEXT")
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "attachments" in tables:
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_attachments_operator_upload_receipt
            ON attachments(tenant_id, review_case_id, reviewer_idempotency_key)
            WHERE reviewer_idempotency_key IS NOT NULL
            """
        )
