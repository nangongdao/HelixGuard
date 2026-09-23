"""Migration 14: csat satisfaction surveys (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(14, "csat satisfaction surveys")
def migration_14(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS qa_spot_checks (
            token TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            review_case_id TEXT NOT NULL,
            rating INTEGER,
            created_at TEXT NOT NULL,
            responded_at TEXT,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_qa_spot_check_conversation
            ON qa_spot_checks(tenant_id, review_case_id);
        """
    )
