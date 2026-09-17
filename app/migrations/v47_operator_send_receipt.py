"""ROADMAP H02 (2.18.0): operator send receipt (expand phase).

The operator workspace can retry a reply whose response was lost — a network
blip, a browser timeout, a process restart. Without a receipt the retry
creates a second customer-visible message and a second ``operator.replied``
audit event, which is exactly what H02 forbids ("发送幂等在服务端成立，不仅禁用
按钮"). The button state is not a guarantee; a durability boundary is.

``messages.operator_idempotency_key`` carries the caller's ``Idempotency-Key``
for the send attempt that produced the row, with a partial unique index over
``(tenant_id, conversation_id, key)``. The index mirrors
``idx_messages_channel_dedup`` (v11) for the same reason: the same key may
replay without a duplicate ever becoming reachable, and the key is namespaced
per conversation so the same string in another conversation is a different
request.

Recording the receipt on the message row — rather than in the
``api_idempotency`` resource-key store (v37) — is deliberate: ``messages`` is
already covered by the archive tier, the DSR erase path, the retention list
and the PostgreSQL RLS policy set, so the receipt inherits every governance
guarantee for free. ``api_idempotency`` has no ``conversation_id`` and appears
in none of those lists, so reusing it would have needed new cleanup paths.

Expand-only: one additive nullable column plus one index.
"""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(47, "operator message send receipt (ROADMAP H02)", phase="expand")
def migrate(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "messages", "operator_idempotency_key", "TEXT")
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "messages" in tables:
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_operator_send_receipt
            ON messages(tenant_id, conversation_id, operator_idempotency_key)
            WHERE operator_idempotency_key IS NOT NULL
            """
        )
