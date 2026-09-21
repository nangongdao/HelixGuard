"""API deprecation lifecycle (ROADMAP 43.3 / docs/API_POLICY.md §2).

A breaking change never deletes an endpoint outright: it is *marked*
deprecated first — every response carries the ``Deprecation`` header (the
date deprecation began) and the ``Sunset`` header (the planned removal
date) — and only removed after the migration window has passed. The
registry below is the single source of truth: one entry per deprecated
operation, enforced by tests so a stale or expired sunset cannot linger.

Design:

* entries are keyed like OpenAPI operations (``"GET /api/conversations"``)
  so both the response-header middleware and the spec enrichment can look
  them up with the same key;
* :func:`active_deprecations` returns only entries whose window still
  applies (sunset in the future); an expired sunset raises in
  ``validate_registry`` so the release gate catches an endpoint that should
  already be gone;
* headers follow the dates format from ``docs/API_POLICY.md``
  (IMF-fixdate, e.g. ``Tue, 14 Aug 2026 00:00:00 GMT``).

The registry drives the domain migration (docs/DOMAIN.md): every renamed path
is declared once in ``_DOMAIN_RENAMES`` and expanded into one entry per HTTP
method, so the old paths keep serving while the new ones take over. The
mechanism, header injection, and gate tests landed in 43.3 — marking any
endpoint was already a data change, not new infrastructure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any
from starlette.datastructures import MutableHeaders

logger = logging.getLogger("helix")

DEPRECATION_HEADER = "Deprecation"
SUNSET_HEADER = "Sunset"


@dataclass(frozen=True)
class Deprecation:
    """One deprecated operation and its removal timeline."""

    operation: str  # e.g. "GET /api/conversations"
    deprecated_on: str  # ISO date the Deprecation header advertises
    sunset_on: str  # ISO date the endpoint will be removed
    successor: str  # path/method clients must migrate to
    reason: str = ""


# Domain migration (docs/DOMAIN.md): the customer-support shell became a
# content-review platform, renaming the API paths below. Each tuple is
# (retired path, HTTP methods still served on it, successor path).
_DOMAIN_RENAMES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    # T6 申诉单
    ("/api/tickets", ("POST", "GET"), "/api/appeals"),
    ("/api/tickets/{appeal_id}", ("GET", "PATCH"), "/api/appeals/{appeal_id}"),
    (
        "/api/tickets/{appeal_id}/transition",
        ("POST",),
        "/api/appeals/{appeal_id}/transition",
    ),
    ("/api/tickets/{appeal_id}/link", ("POST",), "/api/appeals/{appeal_id}/link"),
    # T7 抽检评分
    ("/api/csat/{token}", ("GET", "POST"), "/api/qa-spot-check/{token}"),
    ("/api/admin/csat-summary", ("GET",), "/api/admin/qa-spot-check-summary"),
    # T8 提交端
    ("/api/widget/sessions", ("POST",), "/api/submission-portal/sessions"),
    (
        "/api/widget/sessions/{conversation_id}/messages",
        ("POST", "GET"),
        "/api/submission-portal/sessions/{conversation_id}/messages",
    ),
    (
        "/api/widget/sessions/{conversation_id}/stream",
        ("GET",),
        "/api/submission-portal/sessions/{conversation_id}/stream",
    ),
    # T9 策略库
    ("/api/knowledge", ("GET", "POST"), "/api/policy"),
    ("/api/knowledge/{article_id}", ("PATCH",), "/api/policy/{article_id}"),
    ("/api/knowledge/drafts", ("POST",), "/api/policy/drafts"),
    ("/api/knowledge/{article_id}/review", ("POST",), "/api/policy/{article_id}/review"),
    ("/api/copilot/knowledge", ("POST",), "/api/copilot/policy"),
    ("/api/copilot/knowledge-draft", ("POST",), "/api/copilot/policy-draft"),
    ("/api/supervisor/knowledge-gaps", ("GET",), "/api/supervisor/policy-gaps"),
    # T12 预置结论
    ("/api/canned-responses", ("GET", "POST"), "/api/canned-verdicts"),
    (
        "/api/canned-responses/{response_id}",
        ("PATCH",),
        "/api/canned-verdicts/{response_id}",
    ),
    (
        "/api/canned-responses/{response_id}/use",
        ("POST",),
        "/api/canned-verdicts/{response_id}/use",
    ),
    # T13 审核组
    ("/api/admin/agent-groups", ("GET", "POST"), "/api/admin/reviewer-groups"),
    (
        "/api/admin/agent-groups/{group_id}",
        ("DELETE",),
        "/api/admin/reviewer-groups/{group_id}",
    ),
    (
        "/api/admin/agent-groups/{group_id}/agents",
        ("POST",),
        "/api/admin/reviewer-groups/{group_id}/reviewers",
    ),
    (
        "/api/admin/agent-groups/{group_id}/agents/{actor_id}",
        ("DELETE",),
        "/api/admin/reviewer-groups/{group_id}/reviewers/{actor_id}",
    ),
    # T4 指派审核员
    ("/api/analytics/costs/by_agent", ("GET",), "/api/analytics/costs/by_reviewer"),
)

_DEPRECATED_ON = "2026-09-21"
# docs/API_POLICY.md §2.2 asks for at least one minor version between marking
# and removal; the 12-month window matches this module's v1 -> v2 policy, and
# ``validate_registry`` turns an overdue sunset into a startup failure so the
# promise cannot quietly rot.
_SUNSET_ON = "2027-09-21"
_RENAME_REASON = (
    "Domain migration: the customer-support shell became content review (docs/DOMAIN.md)."
)

_DEPRECATIONS: tuple[Deprecation, ...] = tuple(
    Deprecation(
        operation=f"{method} {path}",
        deprecated_on=_DEPRECATED_ON,
        sunset_on=_SUNSET_ON,
        successor=successor,
        reason=_RENAME_REASON,
    )
    for path, methods, successor in _DOMAIN_RENAMES
    for method in methods
)

_REGISTRY: dict[str, Deprecation] = {entry.operation: entry for entry in _DEPRECATIONS}


def _iso_to_http(iso_date: str) -> str:
    """Render an ISO date as the IMF-fixdate the headers require."""
    parsed = datetime.fromisoformat(f"{iso_date}T00:00:00+00:00")
    return format_datetime(parsed.replace(tzinfo=UTC), usegmt=True)


def deprecation_for(operation: str) -> Deprecation | None:
    """The registered deprecation for an operation key, if any."""
    return _REGISTRY.get(operation)


def active_deprecations(*, today: str | None = None) -> list[Deprecation]:
    """Entries whose sunset has not passed yet (sorted by sunset date)."""
    now = today or datetime.now(UTC).date().isoformat()
    return sorted(
        (entry for entry in _DEPRECATIONS if entry.sunset_on >= now),
        key=lambda entry: entry.sunset_on,
    )


def validate_registry(*, today: str | None = None) -> list[str]:
    """Gate violations: expired sunsets or inverted windows.

    An empty list means the registry is coherent; a non-empty list blocks
    release (an expired sunset means the endpoint should already be gone).
    """
    now = today or datetime.now(UTC).date().isoformat()
    problems: list[str] = []
    for entry in _DEPRECATIONS:
        if entry.sunset_on < entry.deprecated_on:
            problems.append(
                f"{entry.operation}: sunset {entry.sunset_on} precedes "
                f"deprecation {entry.deprecated_on}"
            )
        if entry.sunset_on < now:
            problems.append(
                f"{entry.operation}: sunset {entry.sunset_on} has passed but the "
                "endpoint is still served — remove it or extend the window explicitly"
            )
    return problems


def apply_deprecation_headers(operation: str, headers: MutableHeaders | dict[str, str]) -> bool:
    """Stamp Deprecation/Sunset onto ``headers`` when the op is deprecated.

    Returns True when headers were added. Called from response paths that
    know their operation key (middleware/dependency layer).
    """
    entry = _REGISTRY.get(operation)
    if entry is None:
        return False
    headers[DEPRECATION_HEADER] = _iso_to_http(entry.deprecated_on)
    headers[SUNSET_HEADER] = _iso_to_http(entry.sunset_on)
    if entry.successor:
        headers.setdefault("Link", f'<{entry.successor}>; rel="successor-version"')
    return True


def enrich_openapi_with_deprecations(paths: dict[str, Any]) -> int:
    """Mark deprecated operations in the OpenAPI document.

    Returns the number of operations marked. Runs inside the snapshot
    builder so ``api/openapi.json`` carries ``deprecated: true`` plus a
    description note pointing at the successor.
    """
    marked = 0
    for entry in _DEPRECATIONS:
        method, _, path = entry.operation.partition(" ")
        item = paths.get(path)
        if not isinstance(item, dict):
            continue
        operation = item.get(method.lower())
        if not isinstance(operation, dict):
            continue
        operation["deprecated"] = True
        note = (
            f"Deprecated since {entry.deprecated_on}; removed on {entry.sunset_on}. "
            f"Migrate to {entry.successor}."
        )
        if entry.reason:
            note = f"{note} {entry.reason}"
        description = str(operation.get("description") or "").rstrip()
        operation["description"] = f"{description}\n\n{note}".strip()
        marked += 1
    return marked


__all__ = [
    "DEPRECATION_HEADER",
    "SUNSET_HEADER",
    "Deprecation",
    "active_deprecations",
    "apply_deprecation_headers",
    "deprecation_for",
    "enrich_openapi_with_deprecations",
    "validate_registry",
]
