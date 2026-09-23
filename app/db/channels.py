"""Persistence for signed inbound channel threads and message receipts."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
from typing import Any
from uuid import uuid4

from app.db._util import to_wire_row, utc_after, utc_now
from app.domain import ReviewCaseStatus


class DatabaseChannelsMixin:
    def get_channel_conversation(
        self, tenant_id: str, account_id: str, external_thread_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT c.* FROM channel_threads ct
                JOIN review_cases c
                  ON c.id = ct.review_case_id AND c.tenant_id = ct.tenant_id
                WHERE ct.tenant_id = ? AND ct.account_id = ?
                  AND ct.external_thread_id = ?""",
                (tenant_id, account_id, external_thread_id),
            ).fetchone()
        return self._conversation_from_row(row)

    def get_or_create_channel_conversation(
        self,
        tenant_id: str,
        account_id: str,
        external_thread_id: str,
        submitter_name: str,
        submitter_ref: str,
        channel: str,
        actor: str,
        sla_minutes: int,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically map one external thread to exactly one conversation."""
        review_case_id = f"conv_{uuid4().hex[:12]}"
        now = utc_now()
        effective_sla = self.resolve_sla_policy(tenant_id, "normal", channel, sla_minutes)
        created = False
        with self.connect() as connection:
            # SQLite takes its write lock before the read; PostgreSQL translates
            # this to the shared transaction advisory lock.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT c.* FROM channel_threads ct
                JOIN review_cases c
                  ON c.id = ct.review_case_id AND c.tenant_id = ct.tenant_id
                WHERE ct.tenant_id = ? AND ct.account_id = ?
                  AND ct.external_thread_id = ?""",
                (tenant_id, account_id, external_thread_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO review_cases
                    (id, tenant_id, submitter_name, submitter_ref, channel, status, priority,
                     sla_due_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'normal', ?, ?, ?)""",
                    (
                        review_case_id,
                        tenant_id,
                        submitter_name,
                        submitter_ref,
                        channel,
                        ReviewCaseStatus.OPEN,
                        utc_after(effective_sla),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """INSERT INTO channel_threads
                    (tenant_id, account_id, external_thread_id, review_case_id,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (tenant_id, account_id, external_thread_id, review_case_id, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM review_cases WHERE tenant_id = ? AND id = ?",
                    (tenant_id, review_case_id),
                ).fetchone()
                created = True

        if created:
            self._invalidate_dashboard(tenant_id)
            self.increment_tenant_usage_review_cases(tenant_id, now[:10])
            self.audit(
                tenant_id,
                review_case_id,
                actor,
                "conversation.created",
                {"channel": channel, "customer_verified": True},
            )
        review_case = self._conversation_from_row(row)
        if review_case is None:
            raise RuntimeError("Channel conversation could not be read back")
        return review_case, created

    def get_channel_webhook_receipt(
        self, tenant_id: str, account_id: str, message_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM channel_webhook_receipts
                WHERE tenant_id = ? AND account_id = ? AND message_id = ?""",
                (tenant_id, account_id, message_id),
            ).fetchone()
        return to_wire_row(row) if row else None

    def claim_channel_webhook_receipt(
        self,
        tenant_id: str,
        account_id: str,
        message_id: str,
        external_thread_id: str,
        review_case_id: str,
        body_sha256: str,
    ) -> tuple[dict[str, Any], bool]:
        """Claim an account-wide message id, returning the winner on replay."""
        created = False
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM channel_webhook_receipts
                WHERE tenant_id = ? AND account_id = ? AND message_id = ?""",
                (tenant_id, account_id, message_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO channel_webhook_receipts
                    (tenant_id, account_id, message_id, external_thread_id,
                     review_case_id, content_sha256, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tenant_id,
                        account_id,
                        message_id,
                        external_thread_id,
                        review_case_id,
                        body_sha256,
                        utc_now(),
                    ),
                )
                row = connection.execute(
                    """SELECT * FROM channel_webhook_receipts
                    WHERE tenant_id = ? AND account_id = ? AND message_id = ?""",
                    (tenant_id, account_id, message_id),
                ).fetchone()
                created = True
        if row is None:
            raise RuntimeError("Channel webhook receipt could not be read back")
        return to_wire_row(row), created
