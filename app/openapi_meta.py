"""OpenAPI metadata enrichment (Phase 25.2).

Every API operation gets a human-readable ``summary``, ``description``,
OpenAPI ``tags`` group, and an RBAC note before the snapshot is generated.
Rather than editing ~60 route decorators, this module centralizes the
metadata in one table and applies it to the built app's routes, so a new
endpoint that is added without metadata is still documented (it appears
under a default tag) and the snapshot gate still catches shape changes.

The enrichment runs inside ``create_app`` right before the app is returned,
so ``/openapi.json`` always reflects it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# route key -> (summary, tags, permission note)
_ENDPOINT_META: dict[str, tuple[str, list[str], str]] = {
    "GET /api/me": ("Current user profile", ["auth"], "any authenticated key"),
    "GET /api/review-cases": (
        "List review_cases",
        ["review_cases"],
        "conversation:read",
    ),
    "POST /api/review-cases": (
        "Create a conversation",
        ["review_cases"],
        "conversation:write",
    ),
    "GET /api/review-cases/{review_case_id}": (
        "Conversation detail with messages, audit events, and summaries",
        ["review_cases"],
        "conversation:read",
    ),
    "PATCH /api/review-cases/{review_case_id}": (
        "Update conversation priority",
        ["review_cases"],
        "operator:act",
    ),
    "PUT /api/review-cases/{review_case_id}/labels": (
        "Replace conversation labels",
        ["review_cases"],
        "operator:act",
    ),
    "POST /api/review-cases/bulk-actions": (
        "Bulk priority/label/claim actions",
        ["review_cases"],
        "operator:act",
    ),
    "GET /api/review-case-labels": (
        "Label catalog with counts",
        ["review_cases"],
        "conversation:read",
    ),
    "GET /api/review-cases/{review_case_id}/messages": (
        "List conversation messages",
        ["review_cases"],
        "conversation:read",
    ),
    "POST /api/review-cases/{review_case_id}/messages": (
        "Send a customer turn (idempotent)",
        ["review_cases"],
        "conversation:write",
    ),
    "POST /api/review-cases/{review_case_id}/claim": (
        "Claim a conversation",
        ["review_cases"],
        "operator:act",
    ),
    "POST /api/review-cases/{review_case_id}/release": (
        "Release a claim",
        ["review_cases"],
        "operator:act",
    ),
    "POST /api/review-cases/{review_case_id}/assign": (
        "Assign to an operator",
        ["review_cases"],
        "operator:act",
    ),
    "POST /api/review-cases/{review_case_id}/accept": (
        "Accept a conversation into human_active",
        ["review_cases"],
        "operator:act",
    ),
    "POST /api/review-cases/{review_case_id}/resolve": (
        "Resolve a conversation",
        ["review_cases"],
        "operator:act",
    ),
    "POST /api/review-cases/{review_case_id}/reopen": (
        "Reopen a resolved conversation",
        ["review_cases"],
        "operator:act",
    ),
    "POST /api/review-cases/{review_case_id}/operator-messages": (
        "Send an operator reply",
        ["review_cases"],
        "operator:act",
    ),
    "POST /api/review-cases/{review_case_id}/notes": (
        "Add an internal note (supports @mention colleagues and reply threads)",
        ["review_cases"],
        "operator:act",
    ),
    "GET /api/admin/qa-spot-check-summary": (
        "Aggregate answered CSAT surveys; days only bounds the per-day trend (readouts are all-history)",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/collaborators": (
        "List tenant actors for the @-mention autocomplete",
        ["collaboration"],
        "operator:act",
    ),
    "PATCH /api/review-cases/{review_case_id}/language": (
        "Set (or clear, with null) the manual language override for a conversation",
        ["review_cases"],
        "conversation:write",
    ),
    "POST /api/review-cases/{review_case_id}/messages/{message_id}/translate": (
        "Translate one customer message; without a provider the original text is echoed",
        ["review_cases"],
        "conversation:write",
    ),
    "GET /api/mentions": (
        "List my mentions (unread first) with the unread count",
        ["collaboration"],
        "conversation:read",
    ),
    "POST /api/mentions/{mention_id}/read": (
        "Mark one of my mentions as read (idempotent)",
        ["collaboration"],
        "conversation:read",
    ),
    "GET /api/review-cases/{review_case_id}/threads": (
        "List internal discussion threads for a conversation",
        ["collaboration"],
        "conversation:read",
    ),
    "GET /api/review-cases/{review_case_id}/events": (
        "Supervisor live view: SSE revision stream for a conversation",
        ["collaboration"],
        "conversation:read",
    ),
    "POST /api/review-cases/{review_case_id}/feedback": (
        "Rate an assistant message",
        ["review_cases"],
        "conversation:read",
    ),
    "POST /api/review-cases/{review_case_id}/turn-jobs": (
        "Enqueue an async turn",
        ["turn-jobs"],
        "conversation:write",
    ),
    "GET /api/turn-jobs": (
        "List turn jobs",
        ["turn-jobs"],
        "conversation:read",
    ),
    "GET /api/turn-jobs/{job_id}": (
        "Fetch a turn job with result",
        ["turn-jobs"],
        "conversation:read",
    ),
    "GET /api/turn-jobs/{job_id}/events": (
        "SSE stream for a turn job",
        ["turn-jobs"],
        "conversation:read",
    ),
    "POST /api/turn-jobs/{job_id}/retry": (
        "Retry a failed turn job",
        ["turn-jobs"],
        "conversation:write",
    ),
    "GET /api/saved-views": (
        "List saved queue views",
        ["queues"],
        "conversation:read",
    ),
    "POST /api/saved-views": (
        "Create a saved queue view",
        ["queues"],
        "conversation:read",
    ),
    "DELETE /api/saved-views/{view_id}": (
        "Delete a saved queue view",
        ["queues"],
        "conversation:read",
    ),
    "GET /api/dashboard": (
        "Queue and quality dashboard indicators",
        ["dashboard"],
        "conversation:read",
    ),
    "GET /api/canned-verdicts": (
        "List canned responses",
        ["canned-verdicts"],
        "conversation:read",
    ),
    "POST /api/canned-verdicts": (
        "Create a canned response",
        ["canned-verdicts"],
        "knowledge:write",
    ),
    "PATCH /api/canned-verdicts/{response_id}": (
        "Update a canned response",
        ["canned-verdicts"],
        "knowledge:write",
    ),
    "DELETE /api/canned-verdicts/{response_id}": (
        "Delete a canned response",
        ["canned-verdicts"],
        "knowledge:write",
    ),
    "GET /api/policy": (
        "List published knowledge articles",
        ["policy"],
        "conversation:read",
    ),
    "POST /api/policy": (
        "Create a published knowledge article",
        ["policy"],
        "knowledge:write",
    ),
    "PATCH /api/policy/{article_id}": (
        "Update a knowledge article",
        ["policy"],
        "knowledge:write",
    ),
    "POST /api/policy/drafts": (
        "Create a knowledge draft (invisible until approved)",
        ["policy"],
        "knowledge:write",
    ),
    "POST /api/policy/{article_id}/review": (
        "Publish or retire a pending knowledge article",
        ["policy"],
        "knowledge:write",
    ),
    "POST /api/review-cases/{review_case_id}/messages/{message_id}/policy-draft": (
        "Create a draft from a negatively-rated message",
        ["policy"],
        "knowledge:write",
    ),
    "GET /api/audit-events": (
        "List audit events",
        ["audit"],
        "metrics:read",
    ),
    "GET /api/audit-archives": (
        "List audit archives",
        ["audit"],
        "metrics:read",
    ),
    "GET /api/audit-archives/{archive_id}": (
        "Get audit archive",
        ["audit"],
        "metrics:read",
    ),
    "GET /api/admin/audit/anchors": (
        "List audit frontier anchors",
        ["audit"],
        "admin:manage",
    ),
    "POST /api/admin/audit/anchors/verify": (
        "Re-verify external audit anchors against the local chain",
        ["audit"],
        "admin:manage",
    ),
    "GET /api/admin/audit/gaps": (
        "List observable audit gap counts",
        ["audit"],
        "admin:manage",
    ),
    "GET /api/supervisor/quality": (
        "Quality buckets by day/risk_category/prompt_version",
        ["quality"],
        "metrics:read",
    ),
    "GET /api/supervisor/policy-gaps": (
        "Negative-feedback turns without knowledge citations",
        ["quality"],
        "metrics:read",
    ),
    "GET /api/system/metrics": (
        "Runtime metrics snapshot",
        ["system"],
        "metrics:read",
    ),
    "GET /api/prompts": (
        "List prompt versions",
        ["prompts"],
        "admin:manage",
    ),
    "POST /api/prompts": (
        "Register a prompt version",
        ["prompts"],
        "admin:manage",
    ),
    "POST /api/prompts/{version_id}/action": (
        "Activate, canary, or rollback a prompt version",
        ["prompts"],
        "admin:manage",
    ),
    "PUT /api/admin/tenants/{tenant_id}/model-policy": (
        "Set tenant model policy",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/admin/tenants/{tenant_id}/model-policy": (
        "Read tenant model policy",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/retention/policies": (
        "List retention policies",
        ["admin"],
        "admin:manage",
    ),
    "PUT /api/retention/policies/{data_type}": (
        "Set a retention policy",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/retention/enforce": (
        "Run retention enforcement",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/data-subject-requests": (
        "List data subject requests",
        ["privacy"],
        "privacy:request",
    ),
    "POST /api/data-subject-requests": (
        "Create a data subject request",
        ["privacy"],
        "privacy:request",
    ),
    "POST /api/data-subject-requests/{request_id}/approve": (
        "Approve a data subject request",
        ["privacy"],
        "privacy:approve",
    ),
    "POST /api/data-subject-requests/{request_id}/execute": (
        "Execute an approved data subject request",
        ["privacy"],
        "privacy:execute",
    ),
    "GET /api/data-subject-requests/{request_id}/export": (
        "Download a data subject export",
        ["privacy"],
        "privacy:execute",
    ),
    "GET /api/privacy/board": (
        "Privacy approval board (redacted refs, SLA posture, failure details)",
        ["privacy"],
        "privacy:manage",
    ),
    "POST /api/privacy/sla/scan": (
        "Re-scan open DSRs and flag SLA breaches",
        ["privacy"],
        "privacy:manage",
    ),
    "POST /api/privacy/requests/{request_id}/retry": (
        "Return a failed DSR to approved for re-execution",
        ["privacy"],
        "privacy:manage",
    ),
    "GET /api/privacy/deletion-proof": (
        "Deletion attestation keyed by the DSR execution secret",
        ["privacy"],
        "privacy:manage",
    ),
    "GET /api/privacy/tombstones": (
        "List recorded customer tombstones",
        ["privacy"],
        "privacy:manage",
    ),
    "GET /api/webhooks": (
        "List webhook endpoints",
        ["webhooks"],
        "admin:manage",
    ),
    "POST /api/webhooks": (
        "Register a webhook endpoint",
        ["webhooks"],
        "admin:manage",
    ),
    "DELETE /api/webhooks/{endpoint_id}": (
        "Delete a webhook endpoint",
        ["webhooks"],
        "admin:manage",
    ),
    "GET /api/webhooks/deliveries": (
        "List webhook deliveries",
        ["webhooks"],
        "admin:manage",
    ),
    "POST /api/webhooks/{endpoint_id}/deliveries/{delivery_id}/retry": (
        "Retry a webhook delivery",
        ["webhooks"],
        "admin:manage",
    ),
    "GET /api/events/queue": (
        "SSE queue updates",
        ["queues"],
        "conversation:read",
    ),
    "GET /api/prompts/export": (
        "Export prompt registry",
        ["prompts"],
        "admin:manage",
    ),
    "POST /api/admin/tenants": (
        "Provision a tenant (idempotent)",
        ["admin"],
        "tenant:manage",
    ),
    "GET /api/admin/tenants/{tenant_id}/quota": (
        "Read tenant quota",
        ["admin"],
        "tenant:manage",
    ),
    "PUT /api/admin/tenants/{tenant_id}/quota": (
        "Update tenant quota",
        ["admin"],
        "tenant:manage",
    ),
    "POST /api/admin/tenants/{tenant_id}/members": (
        "Invite a tenant member",
        ["admin"],
        "tenant:manage",
    ),
    "GET /api/admin/tenants/{tenant_id}/members": (
        "List tenant members",
        ["admin"],
        "tenant:manage",
    ),
    "PATCH /api/admin/tenants/{tenant_id}/members/{actor_id}": (
        "Change a member's role",
        ["admin"],
        "tenant:manage",
    ),
    "POST /api/admin/tenants/{tenant_id}/members/{actor_id}/deactivate": (
        "Deactivate a tenant member",
        ["admin"],
        "tenant:manage",
    ),
    "GET /api/admin/usage": (
        "Export raw daily tenant usage",
        ["admin"],
        "tenant:manage",
    ),
    "GET /api/admin/diagnostics": (
        "Support diagnostics bundle (version/config/queue/audit head)",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/admin/reviewer-groups": (
        "List agent groups (skills + capacity)",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/admin/reviewer-groups": (
        "Create an agent group",
        ["admin"],
        "admin:manage",
    ),
    "DELETE /api/admin/reviewer-groups/{group_id}": (
        "Delete an agent group",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/admin/reviewer-groups/{group_id}/reviewers": (
        "Add an agent to a group",
        ["admin"],
        "admin:manage",
    ),
    "DELETE /api/admin/reviewer-groups/{group_id}/reviewers/{actor_id}": (
        "Remove an agent from a group",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/admin/sla-policies": (
        "List SLA policies (tenant/priority/channel limits)",
        ["admin"],
        "admin:manage",
    ),
    "PUT /api/admin/sla-policies": (
        "Configure an SLA policy",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/admin/routing-rules": (
        "List routing rules",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/admin/routing-rules": (
        "Create a routing rule (risk_category/label/channel -> group)",
        ["admin"],
        "admin:manage",
    ),
    "DELETE /api/admin/routing-rules/{rule_id}": (
        "Delete a routing rule",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/admin/keys/{credential_id}/revoke": (
        "Revoke an API key by credential id",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/admin/keys": (
        "Issue a fresh runtime API key (secret returned once)",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/admin/keys": (
        "List API-key credentials (secrets are never returned)",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/submission-portal/sessions": (
        "Open a widget chat session (signed token)",
        ["submission_portal"],
        "X-Widget-Token (signed, no API key)",
    ),
    "POST /api/channels/{account_id}/webhook": (
        "Receive a signed formal-channel customer message",
        ["channels"],
        "X-Helix-Timestamp + raw-body HMAC signature (no API key)",
    ),
    "POST /api/submission-portal/sessions/{review_case_id}/messages": (
        "Send a widget message (channel-id idempotent)",
        ["submission_portal"],
        "X-Widget-Token (signed, no API key)",
    ),
    "GET /api/submission-portal/sessions/{review_case_id}/messages": (
        "List widget conversation messages",
        ["submission_portal"],
        "X-Widget-Token (signed, no API key)",
    ),
    "GET /api/submission-portal/sessions/{review_case_id}/stream": (
        "SSE stream for the widget conversation's latest turn",
        ["submission_portal"],
        "X-Widget-Token (signed, no API key)",
    ),
    "POST /api/qa-spot-check/{token}": (
        "Submit a one-time CSAT satisfaction rating",
        ["qa_spot_check"],
        "one-time survey token (no API key)",
    ),
    "POST /api/copilot/suggest": (
        "Suggest 1-3 customer-facing reply drafts for a conversation",
        ["copilot"],
        "operator:act",
    ),
    "POST /api/copilot/policy": (
        "Recommend knowledge articles for the latest customer message",
        ["copilot"],
        "operator:act",
    ),
    "POST /api/copilot/rewrite": (
        "Rewrite an operator draft in a requested tone (best-effort)",
        ["copilot"],
        "operator:act",
    ),
    "POST /api/appeals": (
        "Convert a conversation into a long-cycle ticket (idempotent)",
        ["appeals"],
        "operator:act",
    ),
    "GET /api/appeals": (
        "List appeals (filter by status or customer reference)",
        ["appeals"],
        "conversation:read",
    ),
    "GET /api/appeals/{appeal_id}": (
        "Ticket detail with linked review_cases",
        ["appeals"],
        "conversation:read",
    ),
    "PATCH /api/appeals/{appeal_id}": (
        "Update ticket subject/description/priority/assignee",
        ["appeals"],
        "operator:act",
    ),
    "POST /api/appeals/{appeal_id}/transition": (
        "Move a ticket through its state machine (open/in_progress/closed)",
        ["appeals"],
        "operator:act",
    ),
    "POST /api/appeals/{appeal_id}/link": (
        "Link another conversation to the ticket (cross-conversation tracking)",
        ["appeals"],
        "operator:act",
    ),
    "POST /api/admin/report-subscriptions": (
        "Create a scheduled report subscription (webhook delivery)",
        ["reports"],
        "admin:manage",
    ),
    "GET /api/admin/report-subscriptions": (
        "List report subscriptions",
        ["reports"],
        "admin:manage",
    ),
    "PATCH /api/admin/report-subscriptions/{subscription_id}": (
        "Update a report subscription (active/schedule/window)",
        ["reports"],
        "admin:manage",
    ),
    "DELETE /api/admin/report-subscriptions/{subscription_id}": (
        "Delete a report subscription",
        ["reports"],
        "admin:manage",
    ),
    "POST /api/admin/reports/generate": (
        "Generate a quality/usage report on demand (optionally deliver)",
        ["reports"],
        "admin:manage",
    ),
    "GET /api/admin/reports/{report_type}/export": (
        "Export a quality/usage report as CSV",
        ["reports"],
        "admin:manage",
    ),
    "POST /api/attachments": (
        "Upload an attachment to a conversation (validated + scanned)",
        ["attachments"],
        "operator:act",
    ),
    "GET /api/attachments": (
        "List a conversation's stored attachments",
        ["attachments"],
        "conversation:read",
    ),
    "GET /api/attachments/{attachment_id}": (
        "Attachment metadata",
        ["attachments"],
        "conversation:read",
    ),
    "GET /api/attachments/{attachment_id}/download": (
        "Force-download an attachment (Content-Disposition: attachment)",
        ["attachments"],
        "conversation:read",
    ),
    "DELETE /api/attachments/{attachment_id}": (
        "Delete an attachment (removes the file and frees quota)",
        ["attachments"],
        "operator:act",
    ),
    "GET /health": ("Liveness probe", ["system"], "none (unauthenticated)"),
    "GET /health/live": ("Liveness probe", ["system"], "none (unauthenticated)"),
    "GET /health/ready": (
        "Readiness probe (database reachable)",
        ["system"],
        "none (unauthenticated)",
    ),
    "GET /health/startup": (
        "Startup probe (app serving, before dependencies ready)",
        ["system"],
        "none (unauthenticated)",
    ),
    "GET /": ("Operator workspace", ["ui"], "none (unauthenticated)"),
    "POST /api/canned-verdicts/{response_id}/use": (
        "Record canned-response usage",
        ["canned-verdicts"],
        "operator:act",
    ),
    "GET /auth/login": ("OIDC login redirect", ["auth"], "none (unauthenticated)"),
    "GET /auth/callback": ("OIDC callback", ["auth"], "none (unauthenticated)"),
    "POST /auth/logout": ("End the session", ["auth"], "any authenticated session"),
    "GET /auth/session": ("Current session", ["auth"], "any authenticated session"),
    "POST /auth/refresh": (
        "Refresh the session cookie",
        ["auth"],
        "any authenticated session",
    ),
}


def _operation_key(route: Any) -> str | None:
    methods = getattr(route, "methods", None)
    if not methods:
        return None
    method = next(iter(methods), "").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return None
    return f"{method} {route.path}"


def _iter_routes(app: Any) -> Any:
    """Yield every APIRoute, including those inside included routers.

    FastAPI 0.139 wraps ``include_router`` targets in a lazy ``_IncludedRouter``
    that is materialized at request time; its inner ``APIRouter.routes`` are
    the real route objects and must be enriched too.
    """
    for route in app.routes:
        if type(route).__name__ == "_IncludedRouter":
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from inner.routes
            continue
        yield route


def enrich_openapi(app: Any) -> None:
    """Apply summaries/tags/permission notes to every registered operation.

    FastAPI stores summary/description/tags on each ``APIRoute`` at
    construction time (from the decorator args or the endpoint docstring).
    Mutating those attributes before the OpenAPI spec is generated is the
    supported hook; the builder reads them via ``get_openapi``.
    """
    for route in _iter_routes(app):
        key = _operation_key(route)
        if key is None:
            continue
        # 43.3: deprecated operations are flagged on the route itself so the
        # generated spec (and clients reading it) see ``deprecated: true``.
        from app.deprecation import deprecation_for

        entry = deprecation_for(key)
        # Domain migration (docs/DOMAIN.md): an operation that still serves its
        # retired path is documented exactly like the successor it points at —
        # same summary, tags and RBAC note — so the migration window does not
        # degrade the generated docs and SDK.
        meta = _ENDPOINT_META.get(key)
        if meta is None and entry is not None:
            method = key.split(" ", 1)[0]
            meta = _ENDPOINT_META.get(f"{method} {entry.successor}")
        if meta is None:
            continue
        summary, tags, permission = meta
        if not getattr(route, "summary", None):
            route.summary = summary
        if not getattr(route, "description", None) or route.description == route.summary:
            route.description = f"{summary}. Requires: {permission}."
        if not getattr(route, "tags", None):
            route.tags = tags
        if entry is not None:
            route.deprecated = True


def apply_openapi_metadata(app: Any) -> Callable[[], dict[str, Any]]:
    """Return an openapi() implementation that enriches the spec first.

    FastAPI 0.139 defers OpenAPI generation until first access; we hook the
    app's ``openapi`` method so summary/description/tags are injected onto
    routes before the spec is built, then call the original builder.
    """
    original_openapi = app.openapi

    def enriched_openapi() -> dict[str, Any]:
        enrich_openapi(app)
        spec = original_openapi()
        # 43.3: deprecated operations also carry the migration note in the
        # document itself (description + successor pointer).
        from app.deprecation import enrich_openapi_with_deprecations

        enrich_openapi_with_deprecations(spec.get("paths", {}))
        return spec

    return enriched_openapi
