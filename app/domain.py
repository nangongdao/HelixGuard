from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ConversationStatus(StrEnum):
    OPEN = "open"
    WAITING_HUMAN = "waiting_human"
    HUMAN_ACTIVE = "human_active"
    RESOLVED = "resolved"


class AgentName(StrEnum):
    POLICY = "policy"
    TRIAGE = "triage"
    KNOWLEDGE = "knowledge"
    ORDER = "order"
    ESCALATION = "escalation"
    QUALITY = "quality"


class TaskOutcome(StrEnum):
    """Closed vocabulary for what a specialist turn did for the customer task.

    ROADMAP H03: "流程完成" is not "问题已解决" -- the persisted outcome kind
    makes 补问 (clarification) distinguishable from a definitive answer and
    from a handoff, without weakening the evidence rule for answers. These
    tokens are persisted (assistant-message metadata, pending-task records),
    so they are a data contract: add-only, never rename.
    """

    CLARIFICATION = "clarification"
    ANSWER = "answer"
    HANDOFF = "handoff"


# The one pending-task kind in this slice (H03): an order query waiting for
# the customer to supply the order number. Future task kinds extend this.
PENDING_TASK_ORDER_CLARIFICATION = "order_clarification"

# Default lifetime of a pending clarification task, in minutes. Deployments
# override it through ``Settings.pending_clarification_ttl_minutes``; the
# constant exists so the store stays usable without a settings object.
DEFAULT_PENDING_TASK_TTL_MINUTES = 120


@dataclass(frozen=True)
class RiskAssessment:
    categories: list[str] = field(default_factory=list)
    requires_human: bool = False
    handoff_reason: str | None = None
    redacted_excerpt: str = ""


@dataclass(frozen=True)
class TriageDecision:
    route: AgentName
    intent: str
    confidence: float
    urgency: str = "normal"
    reasons: list[str] = field(default_factory=list)
    mode: str = "rules"


@dataclass(frozen=True)
class AgentResult:
    agent: AgentName
    content: str
    confidence: float
    citations: list[dict[str, str]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    requires_human: bool = False
    handoff_reason: str | None = None
    # H03: outcome kind from the closed ``TaskOutcome`` vocabulary; ``None``
    # means the result predates the contract (legacy constructions and
    # agent-level escalations) and takes no part in pending-task bookkeeping.
    task_outcome: str | None = None
    # Set only when ``task_outcome`` is ``clarification``: the slot the task
    # waits on (order flow: ``order_id``).
    clarify_slot: str | None = None


@dataclass(frozen=True)
class QualityAssessment:
    approved: bool
    issues: list[str] = field(default_factory=list)


class TurnInProgressError(ValueError):
    """A turn is already being processed for the conversation."""


class InvalidTransitionError(ValueError):
    """The conversation lifecycle rejects the requested transition."""


class IdempotencyConflictError(ValueError):
    """A request id was reused with a different payload."""
