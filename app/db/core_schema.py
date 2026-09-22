"""Database core_schema mixin (Phase 27.1, extracted from app/database.py)."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import sqlite3


class DatabaseCoreSchemaMixin:
    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
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
                CREATE TRIGGER IF NOT EXISTS messages_tenant_guard
                BEFORE INSERT ON messages
                WHEN NOT EXISTS (
                    SELECT 1 FROM review_cases c
                    WHERE c.id = NEW.review_case_id AND c.tenant_id = NEW.tenant_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'conversation tenant mismatch');
                END;
                CREATE TRIGGER IF NOT EXISTS feedback_tenant_guard
                BEFORE INSERT ON feedback
                WHEN NOT EXISTS (
                    SELECT 1 FROM messages m
                    WHERE m.id = NEW.message_id
                      AND m.review_case_id = NEW.review_case_id
                      AND m.tenant_id = NEW.tenant_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'message tenant mismatch');
                END;
                CREATE TRIGGER IF NOT EXISTS turn_jobs_tenant_guard
                BEFORE INSERT ON turn_jobs
                WHEN NOT EXISTS (
                    SELECT 1 FROM review_cases c
                    WHERE c.id = NEW.review_case_id AND c.tenant_id = NEW.tenant_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'conversation tenant mismatch');
                END;
                CREATE TRIGGER IF NOT EXISTS review_case_labels_tenant_guard
                BEFORE INSERT ON review_case_labels
                WHEN NOT EXISTS (
                    SELECT 1 FROM review_cases c
                    WHERE c.id = NEW.review_case_id AND c.tenant_id = NEW.tenant_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'conversation tenant mismatch');
                END;
                """
            )
            self._ensure_column(connection, "review_cases", "submitter_ref", "TEXT")
            self._ensure_column(connection, "review_cases", "handoff_reason", "TEXT")
            self._ensure_column(connection, "review_cases", "sla_due_at", "TEXT")
            self._ensure_column(connection, "review_cases", "last_confidence", "REAL")
            self._ensure_column(connection, "review_cases", "version", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(connection, "review_cases", "preview", "TEXT")
            self._ensure_column(
                connection, "review_cases", "message_count", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(connection, "review_cases", "last_message_at", "TEXT")
            self._ensure_column(
                connection, "review_cases", "labels_json", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._ensure_column(connection, "review_cases", "claimed_by", "TEXT")
            self._ensure_column(connection, "review_cases", "claimed_at", "TEXT")
            self._ensure_column(connection, "review_cases", "claim_expires_at", "TEXT")
            self._ensure_column(
                connection, "review_cases", "needs_response", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(connection, "review_cases", "waiting_since", "TEXT")
            self._ensure_column(connection, "review_cases", "first_response_at", "TEXT")
            self._ensure_column(connection, "review_cases", "decided_at", "TEXT")
            self._ensure_column(connection, "messages", "turn_id", "TEXT")
            self._ensure_column(
                connection, "policy_articles", "category", "TEXT NOT NULL DEFAULT 'general'"
            )
            self._ensure_column(
                connection, "policy_articles", "version", "INTEGER NOT NULL DEFAULT 1"
            )
            self._ensure_column(connection, "source_lookups", "submitter_ref", "TEXT")
            self._ensure_column(connection, "audit_events", "request_id", "TEXT")
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_queue
                ON review_cases(tenant_id, status, priority, sla_due_at)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_page
                ON review_cases(tenant_id, status, priority, updated_at DESC, id)"""
            )
            # Queue default sort is ``priority``, whose ORDER BY ranks via a CASE
            # expression, so the plain ``idx_review_cases_page`` can't serve it
            # and PostgreSQL re-sorts the whole tenant on every offset page
            # (CAPACITY 3.2 PG: queue.offset P95 348ms before this index).  An
            # expression index matching the exact ORDER BY turns each queue page
            # into an index scan (PG full-tenant 100k: ~8ms).  The trailing
            # ``id DESC`` is load-bearing: the ORDER BY is ``updated_at DESC,
            # id DESC``, and PostgreSQL matches index columns per-slot, so an
            # ascending `id` disables the whole index as a sort source.
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_priority_page
                ON review_cases(
                    tenant_id,
                    (CASE priority WHEN 'high' THEN 0 ELSE 1 END),
                    updated_at DESC,
                    id DESC
                )"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_assigned
                ON review_cases(tenant_id, assigned_reviewer, status, updated_at DESC)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_claimed
                ON review_cases(tenant_id, claimed_by, claim_expires_at)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_priority
                ON review_cases(tenant_id, priority, status, updated_at DESC)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_sla
                ON review_cases(tenant_id, status, sla_due_at)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_response_queue
                ON review_cases(tenant_id, needs_response, priority, updated_at DESC, id)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_waiting
                ON review_cases(tenant_id, needs_response, waiting_since, id)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_channel
                ON review_cases(tenant_id, channel, status, updated_at DESC)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_review_cases_sla_sort
                ON review_cases(tenant_id, status, sla_due_at, id)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_audit_tenant_type
                ON audit_events(tenant_id, event_type, created_at DESC, id)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_messages_tenant_role
                ON messages(tenant_id, role, created_at DESC)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_messages_page
                ON messages(tenant_id, review_case_id, created_at, id)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_audit_tenant_created
                ON audit_events(tenant_id, created_at DESC, id)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_feedback_tenant_rating
                ON feedback(tenant_id, rating)"""
            )
            connection.execute(
                """CREATE INDEX IF NOT EXISTS idx_source_lookups_customer_lookup
                ON source_lookups(tenant_id, submitter_ref COLLATE NOCASE, id COLLATE NOCASE)"""
            )
            connection.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS messages_summary_insert
                AFTER INSERT ON messages
                BEGIN
                    UPDATE review_cases
                    SET message_count = message_count + 1,
                        preview = CASE
                            WHEN last_message_at IS NULL OR NEW.created_at >= last_message_at
                            THEN NEW.content ELSE preview END,
                        last_message_at = CASE
                            WHEN last_message_at IS NULL OR NEW.created_at >= last_message_at
                            THEN NEW.created_at ELSE last_message_at END,
                        updated_at = CASE
                            WHEN NEW.created_at >= updated_at THEN NEW.created_at ELSE updated_at END,
                        version = version + 1
                    WHERE tenant_id = NEW.tenant_id AND id = NEW.review_case_id;
                END;
                CREATE TRIGGER IF NOT EXISTS messages_summary_delete
                AFTER DELETE ON messages
                BEGIN
                    UPDATE review_cases
                    SET message_count = MAX(0, message_count - 1),
                        preview = (
                            SELECT content FROM messages
                            WHERE tenant_id = OLD.tenant_id
                              AND review_case_id = OLD.review_case_id
                            ORDER BY created_at DESC, rowid DESC LIMIT 1
                        ),
                        last_message_at = (
                            SELECT created_at FROM messages
                            WHERE tenant_id = OLD.tenant_id
                              AND review_case_id = OLD.review_case_id
                            ORDER BY created_at DESC, rowid DESC LIMIT 1
                        ),
                        version = version + 1
                    WHERE tenant_id = OLD.tenant_id AND id = OLD.review_case_id;
                END;
                CREATE TRIGGER IF NOT EXISTS messages_response_state_insert
                AFTER INSERT ON messages
                WHEN NEW.role IN ('customer', 'assistant', 'operator')
                BEGIN
                    UPDATE review_cases
                    SET needs_response = CASE WHEN NEW.role = 'customer' THEN 1 ELSE 0 END,
                        waiting_since = CASE
                            WHEN NEW.role = 'customer' THEN NEW.created_at ELSE NULL END,
                        first_response_at = CASE
                            WHEN NEW.role IN ('assistant', 'operator')
                            THEN COALESCE(first_response_at, NEW.created_at)
                            ELSE first_response_at END
                    WHERE tenant_id = NEW.tenant_id AND id = NEW.review_case_id;
                END;
                CREATE TRIGGER IF NOT EXISTS messages_response_state_delete
                AFTER DELETE ON messages
                WHEN OLD.role IN ('customer', 'assistant', 'operator')
                BEGIN
                    UPDATE review_cases
                    SET needs_response = CASE WHEN (
                            SELECT role FROM messages
                            WHERE tenant_id = OLD.tenant_id
                              AND review_case_id = OLD.review_case_id
                              AND role IN ('customer', 'assistant', 'operator')
                            ORDER BY created_at DESC, rowid DESC LIMIT 1
                        ) = 'customer' THEN 1 ELSE 0 END,
                        waiting_since = CASE WHEN (
                            SELECT role FROM messages
                            WHERE tenant_id = OLD.tenant_id
                              AND review_case_id = OLD.review_case_id
                              AND role IN ('customer', 'assistant', 'operator')
                            ORDER BY created_at DESC, rowid DESC LIMIT 1
                        ) = 'customer' THEN (
                            SELECT created_at FROM messages
                            WHERE tenant_id = OLD.tenant_id
                              AND review_case_id = OLD.review_case_id
                              AND role = 'customer'
                            ORDER BY created_at DESC, rowid DESC LIMIT 1
                        ) ELSE NULL END
                    WHERE tenant_id = OLD.tenant_id AND id = OLD.review_case_id;
                END;
                CREATE TRIGGER IF NOT EXISTS messages_seq_fill
                AFTER INSERT ON messages
                BEGIN
                    UPDATE messages SET seq = rowid WHERE id = NEW.id;
                END;
                CREATE TRIGGER IF NOT EXISTS audit_events_seq_fill
                AFTER INSERT ON audit_events
                BEGIN
                    UPDATE audit_events SET seq = rowid WHERE id = NEW.id;
                END;
                """
            )
            connection.execute(
                """UPDATE review_cases
                SET message_count = (
                        SELECT COUNT(*) FROM messages m
                        WHERE m.tenant_id = review_cases.tenant_id
                          AND m.review_case_id = review_cases.id
                    ),
                    preview = (
                        SELECT content FROM messages m
                        WHERE m.tenant_id = review_cases.tenant_id
                          AND m.review_case_id = review_cases.id
                        ORDER BY created_at DESC, rowid DESC LIMIT 1
                    ),
                    last_message_at = (
                        SELECT created_at FROM messages m
                        WHERE m.tenant_id = review_cases.tenant_id
                          AND m.review_case_id = review_cases.id
                        ORDER BY created_at DESC, rowid DESC LIMIT 1
                    )
                WHERE EXISTS (
                    SELECT 1 FROM messages m
                    WHERE m.tenant_id = review_cases.tenant_id
                      AND m.review_case_id = review_cases.id
                ) AND (message_count = 0 OR last_message_at IS NULL)"""
            )
            connection.execute(
                """UPDATE review_cases
                SET needs_response = CASE WHEN (
                        SELECT role FROM messages m
                        WHERE m.tenant_id = review_cases.tenant_id
                          AND m.review_case_id = review_cases.id
                          AND m.role IN ('customer', 'assistant', 'operator')
                        ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1
                    ) = 'customer' THEN 1 ELSE 0 END,
                    waiting_since = CASE WHEN (
                        SELECT role FROM messages m
                        WHERE m.tenant_id = review_cases.tenant_id
                          AND m.review_case_id = review_cases.id
                          AND m.role IN ('customer', 'assistant', 'operator')
                        ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1
                    ) = 'customer' THEN (
                        SELECT created_at FROM messages m
                        WHERE m.tenant_id = review_cases.tenant_id
                          AND m.review_case_id = review_cases.id
                          AND m.role = 'customer'
                        ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1
                    ) ELSE NULL END
                WHERE EXISTS (
                    SELECT 1 FROM messages m
                    WHERE m.tenant_id = review_cases.tenant_id
                      AND m.review_case_id = review_cases.id
                )"""
            )
            connection.execute(
                """UPDATE review_cases
                SET labels_json = COALESCE((
                    SELECT json_group_array(label) FROM (
                        SELECT label FROM review_case_labels l
                        WHERE l.tenant_id = review_cases.tenant_id
                          AND l.review_case_id = review_cases.id
                        ORDER BY l.created_at, l.label
                    )
                ), '[]')
                WHERE labels_json = '[]' AND EXISTS (
                    SELECT 1 FROM review_case_labels l
                    WHERE l.tenant_id = review_cases.tenant_id
                      AND l.review_case_id = review_cases.id
                )"""
            )
            self._initialize_policy_fts(connection)
            self._initialize_message_fts(connection)

            # Ensure data governance tables exist (added by migration framework).
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS retention_policies (
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    data_type TEXT NOT NULL,
                    retention_days INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, data_type)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS data_subject_requests (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    submitter_ref TEXT NOT NULL,
                    request_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    requested_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )

            # Run versioned migrations on top of the legacy schema.
            from app.migrations import all_migrations, run_migrations

            run_migrations(connection, all_migrations())
        self.seed_demo()

    def _initialize_policy_fts(self, connection: sqlite3.Connection) -> None:
        # 43.2 bullet 3: no field becomes searchable/indexable before it is
        # classified. The gate fails loud at startup if the FTS column set
        # ever grows without a FIELD_REGISTRY entry, instead of silently
        # downgrading the encryption/redaction posture of an unclassified
        # field.
        from app.envelope_crypto import ensure_searchable_fields_are_classified

        ensure_searchable_fields_are_classified(
            ["title", "content", "tags", "category", "search_terms"]
        )
        try:
            connection.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS policy_fts USING fts5(
                    article_id UNINDEXED,
                    tenant_id UNINDEXED,
                    title,
                    content,
                    tags,
                    category,
                    search_terms,
                    tokenize = 'unicode61 remove_diacritics 2'
                )"""
            )
            connection.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS policy_fts_insert
                AFTER INSERT ON policy_articles
                BEGIN
                    INSERT INTO policy_fts(
                        article_id, tenant_id, title, content, tags, category, search_terms
                    ) VALUES (
                        NEW.id, NEW.tenant_id, NEW.title, NEW.content, NEW.tags, NEW.category,
                        helix_search_terms(
                            NEW.tags || ' ' || NEW.title || ' ' || NEW.category || ' ' || NEW.content
                        )
                    );
                END;
                CREATE TRIGGER IF NOT EXISTS policy_fts_update
                AFTER UPDATE OF tenant_id, title, content, tags, category ON policy_articles
                BEGIN
                    DELETE FROM policy_fts
                    WHERE article_id = OLD.id AND tenant_id = OLD.tenant_id;
                    INSERT INTO policy_fts(
                        article_id, tenant_id, title, content, tags, category, search_terms
                    ) VALUES (
                        NEW.id, NEW.tenant_id, NEW.title, NEW.content, NEW.tags, NEW.category,
                        helix_search_terms(
                            NEW.tags || ' ' || NEW.title || ' ' || NEW.category || ' ' || NEW.content
                        )
                    );
                END;
                CREATE TRIGGER IF NOT EXISTS policy_fts_delete
                AFTER DELETE ON policy_articles
                BEGIN
                    DELETE FROM policy_fts
                    WHERE article_id = OLD.id AND tenant_id = OLD.tenant_id;
                END;
                """
            )
            connection.execute(
                """INSERT INTO policy_fts(
                    article_id, tenant_id, title, content, tags, category, search_terms
                )
                SELECT k.id, k.tenant_id, k.title, k.content, k.tags, k.category,
                       helix_search_terms(
                           k.tags || ' ' || k.title || ' ' || k.category || ' ' || k.content
                       )
                FROM policy_articles k
                WHERE NOT EXISTS (
                    SELECT 1 FROM policy_fts f
                    WHERE f.article_id = k.id AND f.tenant_id = k.tenant_id
                )"""
            )
        except sqlite3.OperationalError:
            self._fts_enabled = False
        else:
            self._fts_enabled = True

    def _initialize_message_fts(self, connection: sqlite3.Connection) -> None:
        # Same 43.2 gate as the knowledge index: message FTS exposes
        # ``content`` (already classified CONFIDENTIAL as conversation body).
        from app.envelope_crypto import ensure_searchable_fields_are_classified

        ensure_searchable_fields_are_classified(["content", "search_terms"])
        try:
            connection.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
                    message_id UNINDEXED,
                    tenant_id UNINDEXED,
                    review_case_id UNINDEXED,
                    content,
                    search_terms,
                    tokenize = 'unicode61 remove_diacritics 2'
                )"""
            )
            connection.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS message_fts_insert
                AFTER INSERT ON messages
                BEGIN
                    INSERT INTO message_fts(
                        message_id, tenant_id, review_case_id, content, search_terms
                    ) VALUES (
                        NEW.id, NEW.tenant_id, NEW.review_case_id, NEW.content,
                        helix_search_terms(NEW.content)
                    );
                END;
                CREATE TRIGGER IF NOT EXISTS message_fts_update
                AFTER UPDATE OF content ON messages
                BEGIN
                    DELETE FROM message_fts WHERE message_id = OLD.id;
                    INSERT INTO message_fts(
                        message_id, tenant_id, review_case_id, content, search_terms
                    ) VALUES (
                        NEW.id, NEW.tenant_id, NEW.review_case_id, NEW.content,
                        helix_search_terms(NEW.content)
                    );
                END;
                CREATE TRIGGER IF NOT EXISTS message_fts_delete
                AFTER DELETE ON messages
                BEGIN
                    DELETE FROM message_fts WHERE message_id = OLD.id;
                END;
                """
            )
            connection.execute(
                """INSERT INTO message_fts(
                    message_id, tenant_id, review_case_id, content, search_terms
                )
                SELECT m.id, m.tenant_id, m.review_case_id, m.content,
                       helix_search_terms(m.content)
                FROM messages m
                WHERE NOT EXISTS (
                    SELECT 1 FROM message_fts f WHERE f.message_id = m.id
                )"""
            )
        except sqlite3.OperationalError:
            self._message_fts_enabled = False
        else:
            self._message_fts_enabled = True

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
