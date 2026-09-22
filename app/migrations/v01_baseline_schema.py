"""Migration 1: baseline schema (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(1, "baseline schema")
def migration_1(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS review_cases (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            submitter_name TEXT NOT NULL,
            submitter_ref TEXT,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            risk_category TEXT,
            assigned_reviewer TEXT,
            priority TEXT NOT NULL DEFAULT 'normal',
            handoff_reason TEXT,
            sla_due_at TEXT,
            last_confidence REAL,
            version INTEGER NOT NULL DEFAULT 1,
            preview TEXT,
            message_count INTEGER NOT NULL DEFAULT 0,
            last_message_at TEXT,
            labels_json TEXT NOT NULL DEFAULT '[]',
            claimed_by TEXT,
            claimed_at TEXT,
            claim_expires_at TEXT,
            needs_response INTEGER NOT NULL DEFAULT 0,
            waiting_since TEXT,
            first_response_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            decided_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_review_cases_tenant_updated
            ON review_cases(tenant_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            review_case_id TEXT NOT NULL REFERENCES review_cases(id),
            turn_id TEXT,
            role TEXT NOT NULL,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(tenant_id, review_case_id, created_at);
        CREATE TABLE IF NOT EXISTS review_case_labels (
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            review_case_id TEXT NOT NULL REFERENCES review_cases(id),
            label TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, review_case_id, label)
        );
        CREATE INDEX IF NOT EXISTS idx_review_case_labels_lookup
            ON review_case_labels(tenant_id, label, review_case_id);
        CREATE TABLE IF NOT EXISTS policy_articles (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            source_url TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_policy_tenant_active
            ON policy_articles(tenant_id, active, updated_at DESC);
        CREATE TABLE IF NOT EXISTS source_lookups (
            id TEXT NOT NULL,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            submitter_ref TEXT,
            submitter_name TEXT NOT NULL,
            status TEXT NOT NULL,
            amount TEXT NOT NULL,
            eta TEXT,
            tracking_code TEXT,
            PRIMARY KEY (tenant_id, id)
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            review_case_id TEXT,
            request_id TEXT,
            actor TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_audit_conversation
            ON audit_events(tenant_id, review_case_id, created_at);
        CREATE TABLE IF NOT EXISTS turn_requests (
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            review_case_id TEXT NOT NULL REFERENCES review_cases(id),
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL,
            response_json TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, review_case_id, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS turn_jobs (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            review_case_id TEXT NOT NULL REFERENCES review_cases(id),
            idempotency_key TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            available_at TEXT NOT NULL,
            locked_at TEXT,
            locked_by TEXT,
            response_json TEXT,
            error_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE (tenant_id, review_case_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_turn_jobs_dispatch
            ON turn_jobs(status, available_at, created_at);
        CREATE INDEX IF NOT EXISTS idx_turn_jobs_tenant
            ON turn_jobs(tenant_id, status, updated_at DESC);
        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            review_case_id TEXT NOT NULL REFERENCES review_cases(id),
            message_id TEXT NOT NULL REFERENCES messages(id),
            actor TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK (rating IN (-1, 1)),
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (tenant_id, message_id, actor)
        );
        CREATE TABLE IF NOT EXISTS canned_verdicts (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            shortcut TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            active INTEGER NOT NULL DEFAULT 1,
            usage_count INTEGER NOT NULL DEFAULT 0,
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_canned_verdict_verdicts_tenant
            ON canned_verdicts(tenant_id, active, updated_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_canned_verdict_verdicts_shortcut
            ON canned_verdicts(tenant_id, shortcut)
            WHERE shortcut IS NOT NULL AND active = 1;
        CREATE TABLE IF NOT EXISTS saved_views (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            actor_id TEXT NOT NULL,
            name TEXT NOT NULL,
            filters_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (tenant_id, actor_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_saved_views_actor
            ON saved_views(tenant_id, actor_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS retention_policies (
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            data_type TEXT NOT NULL,
            retention_days INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, data_type)
        );
        CREATE TABLE IF NOT EXISTS data_subject_requests (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            submitter_ref TEXT NOT NULL,
            request_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        """
    )
