"""Rich-media attachment routes (backlog: 语音/富媒体消息).

Operator uploads an attachment to a conversation; the file is validated
(type allowlist, per-file cap, per-tenant quota, deterministic scan) and
stored on disk. Upload/delete require ``operator:act``; listing, metadata,
and forced download require ``conversation:read``. Limit violations are 413,
unsupported types 415, missing rows 404.

ROADMAP H02 (2.19.0): an upload may carry an ``Idempotency-Key``. The receipt
is persisted on the attachment row, so a retry after a lost response resolves
to the row the first attempt already produced (``X-Idempotent-Replay: true``)
instead of storing the bytes — and charging the tenant quota — a second time.
A key replayed with a *different* file is a 409, never a silent success.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse

from app.attachments import (
    AttachmentLimitError,
    AttachmentService,
    AttachmentTypeError,
    sanitize_filename,
)
from app.main import require_permission
from app.routers.common import RouteDeps
from app.schemas import AttachmentOut, ScanVerdictIn
from app.security import Principal

# A key is bound to one upload attempt: its author and the exact bytes. A key
# replayed with a different file (name, type, size or digest) is a client bug
# that must fail loudly — returning the earlier attachment would silently
# substitute the file the operator just corrected, which is the "lose the
# draft" failure H02 forbids.
_UPLOAD_RECEIPT_MISMATCH = (
    "Idempotency-Key was already used for a different upload attempt in this conversation"
)

# ``storage_key`` is the internal disk filename and ``reviewer_idempotency_key``
# is the receipt; neither is part of the API contract. ``_out`` filters rather
# than whitelists, so every column that must stay private has to be named here.
_PRIVATE_ATTACHMENT_COLUMNS = frozenset(
    {"storage_key", "reviewer_idempotency_key", "operator_idempotency_key"}
)


def _attachment_upload_receipt(
    database: Any,
    principal: Principal,
    review_case_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    idempotency_key: str | None,
) -> dict[str, Any] | None:
    """Resolve the stored receipt for this upload attempt.

    Returns the stored row when the attempt already landed, ``None`` when this
    is a first upload (or no key was supplied), and raises ``409`` when the key
    is replaying a *different* attempt.
    """
    if not idempotency_key:
        return None
    stored = database.get_attachment_by_operator_key(
        principal.tenant_id, review_case_id, idempotency_key
    )
    if stored is None:
        return None
    if (
        stored.get("uploader") != principal.actor_id
        or stored.get("filename") != sanitize_filename(filename)
        or stored.get("content_type") != content_type
        or int(stored.get("size_bytes") or 0) != len(data)
        or (stored.get("sha256") or "") != hashlib.sha256(data).hexdigest()
    ):
        raise HTTPException(status_code=409, detail=_UPLOAD_RECEIPT_MISMATCH)
    return stored


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    attachments: AttachmentService = deps.services.attachments
    database = deps.database

    def _out(attachment: dict[str, Any]) -> AttachmentOut:
        return AttachmentOut(
            **{
                key: value
                for key, value in attachment.items()
                if key not in _PRIVATE_ATTACHMENT_COLUMNS
            }
        )

    @router.post("/api/attachments", response_model=AttachmentOut, status_code=201)
    async def upload_attachment(
        response: Response,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
        conversation_id: Annotated[str, Form(min_length=5, max_length=80)],
        file: Annotated[UploadFile, File()],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key", max_length=128),
        ] = None,
    ) -> AttachmentOut:
        data = await file.read()
        content_type = (file.content_type or "").split(";")[0].strip().lower()
        filename = file.filename or "attachment"

        # ROADMAP H02: the upload receipt. A lost response must not cost the
        # tenant a second copy of the same bytes.
        replay = _attachment_upload_receipt(
            database, principal, conversation_id, filename, content_type, data, idempotency_key
        )
        if replay is not None:
            response.headers["X-Idempotent-Replay"] = "true"
            return _out(replay)

        try:
            attachment = attachments.upload(
                principal.tenant_id,
                conversation_id,
                principal.actor_id,
                filename,
                content_type,
                data,
                idempotency_key=idempotency_key,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AttachmentTypeError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except AttachmentLimitError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except sqlite3.IntegrityError:
            # Lost the race against a concurrent attempt carrying the same key:
            # the winner's receipt is authoritative. A different file surfaces
            # as a conflict through the same receipt check.
            if not idempotency_key:
                raise
            winner = _attachment_upload_receipt(
                database,
                principal,
                conversation_id,
                filename,
                content_type,
                data,
                idempotency_key,
            )
            if winner is None:
                raise
            response.headers["X-Idempotent-Replay"] = "true"
            return _out(winner)
        return _out(attachment)

    @router.get("/api/attachments", response_model=list[AttachmentOut])
    def list_attachments(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        conversation_id: Annotated[str, Query(min_length=5, max_length=80)],
    ) -> list[AttachmentOut]:
        items = attachments.list_for_conversation(principal.tenant_id, conversation_id)
        return [_out(item) for item in items]

    @router.get("/api/attachments/{attachment_id}", response_model=AttachmentOut)
    def get_attachment(
        attachment_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> AttachmentOut:
        attachment = attachments.get(principal.tenant_id, attachment_id)
        if attachment is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return _out(attachment)

    @router.get("/api/attachments/{attachment_id}/download")
    def download_attachment(
        attachment_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        token: Annotated[str | None, Query(max_length=128)] = None,
        expires: Annotated[int | None, Query(ge=0, le=2**40)] = None,
    ) -> FileResponse:
        result = attachments.download(principal.tenant_id, attachment_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        if token is not None or expires is not None:
            # Signed-URL mode (42.4): the HMAC must validate for THIS
            # tenant+attachment+expiry; an expired or tampered link fails.
            if (
                token is None
                or expires is None
                or not attachments.verify_download_url_token(
                    principal.tenant_id, attachment_id, token, int(expires)
                )
            ):
                raise HTTPException(status_code=403, detail="Invalid or expired download token")
        path, filename, content_type = result
        # FileResponse builds the Content-Disposition itself (RFC 6266
        # ``filename*=utf-8''`` encoding); a hand-built header here could carry
        # raw quotes/CRLF from a hostile filename. The extra headers force the
        # browser to treat the payload as a file, never as active content.
        return FileResponse(
            path=path,
            media_type=content_type,
            filename=filename,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @router.post(
        "/api/attachments/{attachment_id}/verdict",
        response_model=AttachmentOut,
        summary="Apply an external malware-scan verdict",
        description=(
            "SEC-006 quarantine flow: an external AV/CDR engine promotes a "
            "quarantined upload to stored (clean) or rejected (infected) with "
            "its verdict. Only quarantined attachments can transition."
        ),
        tags=["Attachments"],
    )
    def post_scan_verdict(
        attachment_id: str,
        payload: ScanVerdictIn,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> AttachmentOut:
        """External AV/CDR callback: promote a quarantined upload (42.4)."""
        try:
            attachment = attachments.finalize_verdict(
                principal.tenant_id,
                attachment_id,
                clean=payload.clean,
                verdict=payload.verdict,
                actor_id=principal.actor_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if attachment is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return _out(attachment)

    @router.delete("/api/attachments/{attachment_id}")
    def delete_attachment(
        attachment_id: str,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> dict[str, bool]:
        deleted = attachments.delete(principal.tenant_id, attachment_id, principal.actor_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return {"deleted": True}

    return router
