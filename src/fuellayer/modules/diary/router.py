from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.diary import service
from fuellayer.modules.diary.schemas import Mutation, calendar_date


def private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/me/diary", tags=["diary"], dependencies=[Depends(private)])
Auth = Annotated[AuthSubject, Depends(require_auth_subject)]
DB = Annotated[AsyncSession, Depends(get_session)]


@router.post("/mutations")
async def mutate(
    body: Mutation,
    auth: Auth,
    db: DB,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)],
) -> dict[str, Any]:
    return await service.mutate(db, auth.subject, key, body)


@router.get("/range")
async def date_range(auth: Auth, db: DB, start: str, end: str) -> dict[str, Any]:
    return await service.read_range(db, auth.subject, start, end)


@router.get("/changes")
async def changes(
    auth: Auth,
    db: DB,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> dict[str, Any]:
    return await service.changes(db, auth.subject, after, limit)


@router.get("/weekly")
async def weekly(auth: Auth, db: DB, start: str, today: str) -> dict[str, Any]:
    try:
        calendar_date(start)
        calendar_date(today)
    except ValueError as exc:
        service.fail(str(exc))
    return await service.weekly(db, auth.subject, start, today)
