from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.preferences import service
from fuellayer.modules.preferences.schemas import PreviewRequest, SaveRequest

router = APIRouter(prefix="/me/preferences", tags=["preferences"])
Auth = Annotated[AuthSubject, Depends(require_auth_subject)]
DB = Annotated[AsyncSession, Depends(get_session)]


@router.get("")
async def get_preferences(auth: Auth, db: DB, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.read(db, auth.subject)


@router.get("/ingredients")
async def ingredients(auth: Auth, db: DB) -> dict[str, Any]:
    await service.owned(db, auth.subject)
    return {"items": service.INGREDIENTS}


@router.get("/target-history")
async def history(auth: Auth, db: DB, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.target_history(db, auth.subject)


@router.post("/preview")
async def preview_preferences(
    body: PreviewRequest, auth: Auth, db: DB, response: Response
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.preview(db, auth.subject, body)


@router.patch("")
async def save_preferences(
    body: SaveRequest,
    auth: Auth,
    db: DB,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.save(db, auth.subject, idempotency_key, body)


@router.post("/next-plan-preview")
async def next_plan(auth: Auth, db: DB, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.next_plan_preview(db, auth.subject)
