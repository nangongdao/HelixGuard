from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

ASCII_TERM_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]+")
CJK_SEQUENCE_PATTERN = re.compile(r"[\u3400-\u9fff]+")

# Domain migration (P4): the data-layer columns were renamed to the review-case
# vocabulary, but the wire contract keeps the legacy keys (plan §3.2, R8).
# Every row -> dict boundary maps the new column names back to the wire keys.
_COLUMN_TO_WIRE = {
    "submitter_name": "customer_name",
    "submitter_ref": "customer_ref",
    "risk_category": "intent",
    "assigned_reviewer": "assigned_agent",
    "decided_at": "resolved_at",
    "appeal_id": "ticket_id",
    "review_case_id": "conversation_id",
    "source_review_case_id": "source_conversation_id",
}


def to_wire_row(row: Any) -> dict[str, Any]:
    """Copy a DB row to a dict, mapping new column names back to wire keys."""
    items: dict[str, Any] = dict(row)
    return {_COLUMN_TO_WIRE.get(key, key): value for key, value in items.items()}


def knowledge_search_terms(value: str, limit: int = 2048) -> list[str]:
    """Return stable, syntax-free terms shared by FTS indexing and querying."""
    normalized = value.casefold()
    terms: list[str] = ASCII_TERM_PATTERN.findall(normalized)
    for sequence in CJK_SEQUENCE_PATTERN.findall(normalized):
        if len(sequence) == 1:
            terms.append(sequence)
            continue
        terms.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return list(dict.fromkeys(terms))[:limit]


def knowledge_search_document(value: str) -> str:
    return " ".join(knowledge_search_terms(value))


def utc_now() -> str:
    """Current UTC time as an ISO-8601 string.

    Microsecond precision matters: ``created_at`` is the primary sort key for
    message transcripts, and IDs are random UUIDs, so a coarser clock lets two
    messages written in the same tick sort arbitrarily — which showed up as
    assistant replies rendering above the customer question that prompted them.
    """
    return datetime.now(UTC).isoformat(timespec="microseconds")


def utc_after(minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(timespec="microseconds")


def utc_after_seconds(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="microseconds")
