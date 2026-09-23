"""Database messages mixin (Phase 27.1, extracted from app/database.py)."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import json
import sqlite3
from typing import Any
from uuid import uuid4

from app.db._util import (
    to_wire_row,
    utc_now,
)


def _message_row_to_item(row: sqlite3.Row, *, include_seq: bool = True) -> dict[str, Any]:
    """Convert a row from ``messages``/``messages_archive`` to the API shape.

    Shared by the hot ``messages`` reads and the archive-tier fallback
    (ROADMAP 18.3) so both return an identical transcript item.
    """
    item = to_wire_row(row)
    item["metadata"] = json.loads(item.pop("metadata_json", "{}") or "{}")
    item.pop("tenant_id", None)
    item.pop("conversation_id", None)
    item.pop("turn_id", None)
    if not include_seq:
        item.pop("seq", None)
    return item


def _query_messages(
    connection: sqlite3.Connection,
    table: str,
    tenant_id: str,
    review_case_id: str,
    *,
    limit: int | None,
    cursor: tuple[str, int] | None,
    before: bool,
) -> list[sqlite3.Row]:
    clauses = ["tenant_id = ?", "review_case_id = ?"]
    values: list[Any] = [tenant_id, review_case_id]
    if cursor:
        created_at, seq = cursor
        if before:
            clauses.append("(created_at < ? OR (created_at = ? AND seq < ?))")
        else:
            clauses.append("(created_at > ? OR (created_at = ? AND seq > ?))")
        values.extend([created_at, created_at, seq])
    where = " AND ".join(clauses)
    order = "created_at DESC, seq DESC" if before else "created_at ASC, seq ASC"
    # ``seq`` is the monotonic insertion-order column (filled from the implicit
    # rowid on SQLite, from a sequence on PostgreSQL), so ordering and
    # pagination stay deterministic even when two messages land in the same
    # microsecond.  It is surfaced to the caller so the API can build the next
    # cursor.
    query = f"SELECT * FROM {table} WHERE {where} ORDER BY {order}"
    if limit is not None:
        query += " LIMIT ?"
        values.append(max(1, limit))
    return connection.execute(query, values).fetchall()


class DatabaseMessagesMixin:
    def add_message(
        self,
        tenant_id: str,
        review_case_id: str,
        role: str,
        author: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        turn_id: str | None = None,
        channel_message_id: str | None = None,
        reply_to: str | None = None,
        reviewer_idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        message_id = f"msg_{uuid4().hex[:12]}"
        now = utc_now()
        with self.connect() as connection:
            # The write path is hot-only (ROADMAP 18.3): messages may only be
            # appended to live review_cases, never to archived ones.
            exists = connection.execute(
                "SELECT 1 FROM review_cases WHERE tenant_id = ? AND id = ?",
                (tenant_id, review_case_id),
            ).fetchone()
            if not exists:
                raise LookupError("conversation not found")
            connection.execute(
                """INSERT INTO messages
                (id, tenant_id, review_case_id, turn_id, role, author, content,
                 metadata_json, created_at, channel_message_id, reply_to,
                 reviewer_idempotency_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id,
                    tenant_id,
                    review_case_id,
                    turn_id,
                    role,
                    author,
                    content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    channel_message_id,
                    reply_to,
                    reviewer_idempotency_key,
                ),
            )
        self._invalidate_dashboard(tenant_id)
        self.increment_tenant_usage_messages(tenant_id, utc_now()[:10])
        return {
            "id": message_id,
            "role": role,
            "author": author,
            "content": content,
            "metadata": metadata or {},
            "created_at": now,
            "reply_to": reply_to,
        }

    def get_message_by_channel_id(
        self, tenant_id: str, review_case_id: str, channel_message_id: str
    ) -> dict[str, Any] | None:
        """Look up a message by its channel message id (Phase 23.2).

        The unique index ``idx_messages_channel_dedup`` guarantees at most one
        message per (tenant, conversation, channel_message_id), so replaying
        the same channel message can never create a second turn.
        """
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE tenant_id = ? AND review_case_id = ? "
                "AND channel_message_id = ?",
                (tenant_id, review_case_id, channel_message_id),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    def get_message_by_operator_key(
        self, tenant_id: str, review_case_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        """Resolve the send receipt for an operator reply (ROADMAP H02).

        The partial unique index ``idx_messages_operator_send_receipt``
        guarantees at most one message per (tenant, conversation, key), so a
        retried send attempt resolves to the message it already produced
        instead of creating a second one. The key is namespaced per
        conversation: the same string elsewhere is a different request.
        """
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE tenant_id = ? AND review_case_id = ? "
                "AND reviewer_idempotency_key = ?",
                (tenant_id, review_case_id, idempotency_key),
            ).fetchone()
        return _message_row_to_item(row, include_seq=False) if row else None

    def list_messages(
        self,
        tenant_id: str,
        review_case_id: str,
        *,
        limit: int | None = None,
        cursor: tuple[str, int] | None = None,
        before: bool = False,
    ) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = _query_messages(
                connection,
                "messages",
                tenant_id,
                review_case_id,
                limit=limit,
                cursor=cursor,
                before=before,
            )
        if not rows and self.get_archived_review_case(tenant_id, review_case_id) is not None:
            # ROADMAP 18.3: transparent read merge — an archived
            # conversation's transcript is served from the cold tier.
            with self.connect() as connection:
                rows = _query_messages(
                    connection,
                    "messages_archive",
                    tenant_id,
                    review_case_id,
                    limit=limit,
                    cursor=cursor,
                    before=before,
                )
        result = [_message_row_to_item(row) for row in rows]
        if before:
            result.reverse()
        return result

    def get_message(
        self, tenant_id: str, review_case_id: str, message_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM messages
                WHERE tenant_id = ? AND review_case_id = ? AND id = ?""",
                (tenant_id, review_case_id, message_id),
            ).fetchone()
        return _message_row_to_item(row, include_seq=False) if row else None
