import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.cooking import service
from fuellayer.modules.cooking.models import CookingCommand, CookingSession
from fuellayer.modules.cooking.schemas import Command, Preview, Start
from fuellayer.modules.recipes.service import get_user


def private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/cooking", tags=["cooking"], dependencies=[Depends(private)])
DB = Annotated[AsyncSession, Depends(get_session)]
Auth = Annotated[AuthSubject, Depends(require_auth_subject)]


@router.post("/sessions", status_code=201)
async def start(payload: Start, db: DB, auth: Auth) -> dict[str, Any]:
    return await service.start(db, auth.subject, payload)


@router.get("/sessions")
async def sessions(db: DB, auth: Auth) -> dict[str, Any]:
    user = await get_user(db, auth.subject)
    rows = await db.scalars(
        select(CookingSession).where(
            CookingSession.user_id == user.id, CookingSession.status.in_(service.UNFINISHED)
        )
    )
    return {"items": [await service.view(db, row) for row in rows]}


@router.get("/sessions/{sid}")
async def session(sid: uuid.UUID, db: DB, auth: Auth) -> dict[str, Any]:
    user = await get_user(db, auth.subject)
    return await service.view(db, await service.owned(db, user.id, sid))


@router.post("/sessions/{sid}/actions")
async def action(sid: uuid.UUID, payload: Command, db: DB, auth: Auth) -> dict[str, Any]:
    return await service.execute(db, auth.subject, sid, payload)


@router.get("/sessions/{sid}/pantry")
async def pantry(sid: uuid.UUID, db: DB, auth: Auth) -> dict[str, Any]:
    user = await get_user(db, auth.subject)
    await service.owned(db, user.id, sid)
    return {"items": await service.pantry(db, user.id)}


@router.post("/sessions/{sid}/consumption-preview")
async def preview(sid: uuid.UUID, payload: Preview, db: DB, auth: Auth) -> dict[str, Any]:
    user = await get_user(db, auth.subject, lock=True)
    row = await service.owned(db, user.id, sid)
    if row.status != "awaiting_confirmation":
        service.fail("review_required", "Open ingredient confirmation first.")
    return await service.preview(db, row, payload.uses)


@router.get("/commands/{request_id}")
async def receipt(request_id: uuid.UUID, db: DB, auth: Auth) -> dict[str, Any]:
    user = await get_user(db, auth.subject)
    row = await db.get(CookingCommand, (user.id, request_id))
    if not row:
        service.fail(
            "command_missing", "No committed result is visible yet. Retry the same request.", 404
        )
    return {
        "committed": row.result,
        "current": await service.view(db, await service.owned(db, user.id, row.session_id)),
    }
