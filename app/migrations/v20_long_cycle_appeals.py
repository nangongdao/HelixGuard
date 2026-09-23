"""Migration 20: long-cycle appeals (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(20, "long-cycle appeals")
def migration_20(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS appeals (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            subject TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT NOT NULL DEFAULT 'normal',
            assigned_reviewer TEXT,
            submitter_name TEXT NOT NULL,
            submitter_ref TEXT,
            source_review_case_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_appeals_tenant_updated
            ON appeals(tenant_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_appeals_tenant_status
            ON appeals(tenant_id, status);
        CREATE TABLE IF NOT EXISTS appeal_review_cases (
            appeal_id TEXT NOT NULL REFERENCES appeals(id),
            review_case_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (appeal_id, review_case_id)
        );
        CREATE INDEX IF NOT EXISTS idx_appeal_review_cases_conv
            ON appeal_review_cases(review_case_id);
        """
    )
    _ensure_column(connection, "review_cases", "appeal_id", "TEXT")
