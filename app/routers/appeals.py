"""Long-cycle appeal routes (backlog: 申诉单化).

Appeals track issues across review cases: a reviewer converts a review case
into an appeal (or links more review cases to an existing one), and the appeal
carries its own open/in_progress/closed lifecycle, decoupled from review-case
state. Writes require ``operator:act``; reads require ``conversation:read``.
Invalid state-machine transitions return 409.

The previous path was ``/api/tickets``; it keeps serving as a deprecated
alias (``docs/API_POLICY.md`` §2) until the sunset registered in
``app/deprecation._DEPRECATIONS``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.appeals import TICKET_STATUSES
from app.main import require_permission
from app.routers.common import RouteDeps, legacy_route
from app.schemas import (
    TicketConversationOut,
    TicketCreateRequest,
    TicketDetail,
    TicketLinkRequest,
    TicketOut,
    TicketTransitionRequest,
    TicketUpdateRequest,
)
from app.security import Principal


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    database = deps.database

    @router.post("/api/appeals", response_model=TicketOut, status_code=201)
    @legacy_route(
        router, "/api/tickets", methods=["POST"], response_model=TicketOut, status_code=201
    )
    def create_appeal(
        payload: TicketCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> TicketOut:
        try:
            ticket = database.create_appeal(
                principal.tenant_id,
                review_case_id=payload.conversation_id,
                subject=payload.subject,
                description=payload.description,
                priority=payload.priority,
                actor_id=principal.actor_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TicketOut(**ticket)

    @router.get("/api/appeals", response_model=list[TicketOut])
    @legacy_route(router, "/api/tickets", methods=["GET"], response_model=list[TicketOut])
    def list_appeals(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        status: Annotated[str | None, Query(max_length=20)] = None,
        submitter_ref: Annotated[str | None, Query(max_length=80)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[TicketOut]:
        if status and status not in TICKET_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {TICKET_STATUSES}")
        appeals = database.list_appeals(
            principal.tenant_id, status=status, submitter_ref=submitter_ref, limit=limit
        )
        return [TicketOut(**ticket) for ticket in appeals]

    @router.get("/api/appeals/{appeal_id}", response_model=TicketDetail)
    @legacy_route(router, "/api/tickets/{appeal_id}", methods=["GET"], response_model=TicketDetail)
    def get_appeal(
        appeal_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> TicketDetail:
        ticket = database.get_appeal(principal.tenant_id, appeal_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        review_cases = database.list_appeal_review_cases(principal.tenant_id, appeal_id)
        return TicketDetail(
            **ticket, conversations=[TicketConversationOut(**item) for item in review_cases]
        )

    @router.patch("/api/appeals/{appeal_id}", response_model=TicketOut)
    @legacy_route(router, "/api/tickets/{appeal_id}", methods=["PATCH"], response_model=TicketOut)
    def update_appeal(
        appeal_id: str,
        payload: TicketUpdateRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> TicketOut:
        changes = payload.model_dump(exclude_unset=True)
        try:
            ticket = database.update_appeal(
                principal.tenant_id, appeal_id, changes, principal.actor_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TicketOut(**ticket)

    @router.post("/api/appeals/{appeal_id}/transition", response_model=TicketOut)
    @legacy_route(
        router, "/api/tickets/{appeal_id}/transition", methods=["POST"], response_model=TicketOut
    )
    def transition_appeal(
        appeal_id: str,
        payload: TicketTransitionRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> TicketOut:
        try:
            ticket = database.transition_appeal(
                principal.tenant_id,
                appeal_id,
                principal.actor_id,
                payload.status,
                reason=payload.reason,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return TicketOut(**ticket)

    @router.post("/api/appeals/{appeal_id}/link", response_model=TicketOut)
    @legacy_route(
        router, "/api/tickets/{appeal_id}/link", methods=["POST"], response_model=TicketOut
    )
    def link_appeal_review_case(
        appeal_id: str,
        payload: TicketLinkRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> TicketOut:
        try:
            ticket = database.link_appeal_review_case(
                principal.tenant_id,
                appeal_id,
                payload.conversation_id,
                principal.actor_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TicketOut(**ticket)

    return router
