"""Shared route dependencies (Phase 27.2).

FastAPI routes in ``app/main.py`` were closures over ``create_app`` locals.
Each domain router in this package receives a :class:`RouteDeps` bundle so the
route bodies keep referencing the same objects without global state. The
bundle is assembled once in ``create_app`` and passed to each
``build_router(deps)`` factory.

``legacy_route`` lives here because every domain router needs it: the domain
migration renames paths, and ``docs/API_POLICY.md`` §2 forbids removing an
endpoint outright, so each renamed operation is registered a second time on the
same router under its previous path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import APIRouter, FastAPI

from app.config import Settings
from app.database import Database
from app.jobs import TurnJobWorker
from app.orchestrator import ConversationOrchestrator
from app.prompts import PromptRegistry
from app.quality import QualityService
from app.webhooks import WebhookService

F = TypeVar("F", bound=Callable[..., Any])


def legacy_route(router: APIRouter | FastAPI, path: str, **kwargs: Any) -> Callable[[F], F]:
    """Register the decorated operation *again* under its previous ``path``.

    A breaking rename never deletes the old path outright: the same endpoint
    keeps serving through the migration window, flagged ``deprecated`` so the
    generated spec (and clients reading it) see the sunset. Pair every call
    with an ``app.deprecation._DEPRECATIONS`` entry, which is what puts the
    ``Deprecation``/``Sunset`` headers on responses to the old path.

    The target is whatever carries the primary decorator: a domain router, or
    the app itself for routes declared inline in ``create_app``. Both expose
    ``add_api_route``; they are unrelated classes in FastAPI, hence the union.

    Stack it inside the primary decorator and pass the *same* router, so the
    module keeps exporting one router and ``include_router`` stays unchanged.
    Pass ``methods`` explicitly -- ``add_api_route`` defaults to GET only, and an
    alias registered without it silently loses every non-GET verb::

        @router.post("/api/appeals", response_model=AppealOut, status_code=201)
        @legacy_route(router, "/api/tickets", methods=["POST"], response_model=AppealOut, status_code=201)
        def create_appeal(...) -> AppealOut: ...

    A warning is not enough here: a GET-only alias still answers ``GET
    /api/tickets`` with 200, so only a test that asserts each retired *verb*
    catches it (see ``tests/test_domain_path_renames.py``).
    """

    def decorator(func: F) -> F:
        router.add_api_route(path, func, deprecated=True, **kwargs)
        return func

    return decorator


@dataclass(frozen=True)
class RouteDeps:
    settings: Settings
    database: Database
    orchestrator: ConversationOrchestrator
    turn_worker: TurnJobWorker
    services: Any  # AppServices
    queue: Any  # TaskQueue
    webhook_service: WebhookService | None
    static_dir: Any  # Path
    oidc_config: Any | None = None
    oidc_authenticator: Any | None = None
    oidc_flow: Any | None = None
    telemetry_metrics: Any | None = None
    prompt_registry: PromptRegistry | None = None
    quality_service: QualityService | None = None
