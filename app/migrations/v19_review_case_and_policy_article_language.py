"""Migration 19: conversation and knowledge article language (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(19, "conversation and knowledge article language")
def migration_19(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "review_cases", "language", "TEXT")
    _ensure_column(connection, "policy_articles", "language", "TEXT")
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "policy_articles" in tables:
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_policy_language ON policy_articles(tenant_id, language)"
        )
