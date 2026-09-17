"""ROADMAP H03 (2.17.0): pending clarification tasks (expand phase).

One durable row per conversation for the task automation is waiting to
continue -- the order-query clarify flow ("查物流 → 请提供订单号 → ORD-…"):

- ``kind``       -- the task vocabulary (today: ``order_clarification``);
- ``slot``       -- the missing piece the customer was asked for (``order_id``);
- ``intent``     -- the original triage intent the task continues;
- ``rounds``     -- how many clarifications were already sent, so the
  execution stage can escalate instead of asking forever;
- ``expires_at`` -- staleness bound; a lapsed task is lazily dropped on read.

Expand-only: a new table, no existing reader or writer is touched.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(46, "Conversation pending clarification tasks (ROADMAP H03)", phase="expand")
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_pending_tasks (
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            conversation_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            slot TEXT NOT NULL,
            intent TEXT NOT NULL DEFAULT '',
            rounds INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, conversation_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_pending_tasks_expiry "
        "ON conversation_pending_tasks(expires_at)"
    )
