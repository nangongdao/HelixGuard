"""Migration 27: signed inbound channel webhook durability (ROADMAP 23.3) (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(27, "signed inbound channel webhook durability (ROADMAP 23.3)")
def migration_27(connection: sqlite3.Connection) -> None:
    """Persist external-thread identity and account-wide message receipts."""
    _ensure_column(connection, "turn_jobs", "channel_message_id", "TEXT")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS channel_threads (
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            account_id TEXT NOT NULL,
            external_thread_id TEXT NOT NULL,
            review_case_id TEXT NOT NULL UNIQUE
                REFERENCES review_cases(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, account_id, external_thread_id)
        );
        CREATE INDEX IF NOT EXISTS idx_channel_threads_conversation
            ON channel_threads(tenant_id, review_case_id);

        CREATE TABLE IF NOT EXISTS channel_webhook_receipts (
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            account_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            external_thread_id TEXT NOT NULL,
            review_case_id TEXT NOT NULL REFERENCES review_cases(id) ON DELETE CASCADE,
            content_sha256 TEXT NOT NULL,
            received_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, account_id, message_id),
            FOREIGN KEY (tenant_id, account_id, external_thread_id)
                REFERENCES channel_threads(tenant_id, account_id, external_thread_id)
                ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_channel_receipts_conversation
            ON channel_webhook_receipts(tenant_id, review_case_id, received_at);

        CREATE TRIGGER IF NOT EXISTS channel_threads_tenant_guard
        BEFORE INSERT ON channel_threads
        WHEN NOT EXISTS (
            SELECT 1 FROM review_cases c
            WHERE c.id = NEW.review_case_id AND c.tenant_id = NEW.tenant_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'conversation tenant mismatch');
        END;

        CREATE TRIGGER IF NOT EXISTS channel_receipts_tenant_guard
        BEFORE INSERT ON channel_webhook_receipts
        WHEN NOT EXISTS (
            SELECT 1 FROM review_cases c
            WHERE c.id = NEW.review_case_id AND c.tenant_id = NEW.tenant_id
        )
        BEGIN
            SELECT RAISE(ABORT, 'conversation tenant mismatch');
        END;
        """
    )
