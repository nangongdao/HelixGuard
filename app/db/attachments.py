"""Database attachments mixin (ROADMAP H02, 2.19.0).

The ``attachments`` table is otherwise owned by ``app/attachments.py``
(``AttachmentService``), which carries storage, scanning and quota policy.
The upload receipt is a plain row lookup with no policy attached, so it lives
here next to the other ``*_by_operator_key`` receipt reads and stays reachable
from a bare ``Database`` — the property the restart test pins.
"""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import sqlite3
from typing import Any

from app.db._util import to_wire_row


class DatabaseAttachmentsMixin:
    """Read-side helpers for operator-uploaded attachments."""

    def get_attachment_by_operator_key(
        self, tenant_id: str, review_case_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        """Resolve the upload receipt for an operator attachment (ROADMAP H02).

        The partial unique index ``idx_attachments_operator_upload_receipt``
        guarantees at most one row per (tenant, conversation, key), so a
        retried upload attempt resolves to the row it already produced instead
        of storing the bytes a second time. The key is namespaced per
        conversation, so the same string elsewhere is a different request.
        """
        with self.connect() as connection:
            row: sqlite3.Row | None = connection.execute(
                "SELECT * FROM attachments WHERE tenant_id = ? AND review_case_id = ? "
                "AND reviewer_idempotency_key = ?",
                (tenant_id, review_case_id, idempotency_key),
            ).fetchone()
        return to_wire_row(row) if row else None
