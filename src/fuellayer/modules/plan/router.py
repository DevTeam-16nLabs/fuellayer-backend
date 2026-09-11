import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.plan import service
from fuellayer.modules.plan.schemas import (
    ConfirmRequest,
    Occurrence,
    PortionConfirmRequest,
    PortionPreviewRequest,
    PreviewRequest,
    UndoRequest,
)

router = APIRouter(prefix="/plan/replacements", tags=["plan"])
Auth = Annotated[AuthSubject, Depends(require_auth_subject)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("/context")
async def context(body: Occurrence, auth: Auth, session: Session, response: Response) -> Any:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.context(session, auth.subject, body)


@router.post("/preview")
async def preview(body: PreviewRequest, auth: Auth, session: Session, response: Response) -> Any:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.preview(session, auth.subject, body)


@router.post("/confirm")
async def confirm(body: ConfirmRequest, auth: Auth, session: Session, response: Response) -> Any:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.confirm(session, auth.subject, body)


@router.post("/undo")
async def undo(body: UndoRequest, auth: Auth, session: Session, response: Response) -> Any:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.undo(session, auth.subject, body.replacement_id)


@router.get("")
async def history(auth: Auth, session: Session, response: Response) -> Any:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.history(session, auth.subject)


@router.get("/{request_id}")
async def status(request_id: uuid.UUID, auth: Auth, session: Session, response: Response) -> Any:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.status(session, auth.subject, request_id)


portions_router = APIRouter(prefix="/plan/portions", tags=["plan"])


@portions_router.post("/context")
async def portion_context(
    body: Occurrence, auth: Auth, session: Session, response: Response
) -> Any:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.portion_context(session, auth.subject, body)


@portions_router.post("/preview")
async def portion_preview(
    body: PortionPreviewRequest, auth: Auth, session: Session, response: Response
) -> Any:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.preview(session, auth.subject, body)


@portions_router.post("/confirm")
async def portion_confirm(
    body: PortionConfirmRequest, auth: Auth, session: Session, response: Response
) -> Any:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.confirm(session, auth.subject, body)


# Both operations share durable receipts and conditional inverse semantics.
portions_router.add_api_route("/undo", undo, methods=["POST"])
portions_router.add_api_route("", history, methods=["GET"])
portions_router.add_api_route("/{request_id}", status, methods=["GET"])
