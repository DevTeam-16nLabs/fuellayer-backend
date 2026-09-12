import asyncio
from functools import lru_cache
from typing import Annotated, Literal

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.config import settings
from fuellayer.core.database import get_session
from fuellayer.modules.foods.models import CatalogueFood

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["fuellayer-backend"]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="fuellayer-backend")


class ReadinessResponse(BaseModel):
    status: Literal["ready"]
    service: Literal["fuellayer-backend"]
    revision: str
    migration: str


@lru_cache
def migration_head() -> str:
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    if len(heads) != 1:
        raise RuntimeError("Expected one packaged Alembic head")
    return heads[0]


@router.get("/ready", response_model=ReadinessResponse)
async def ready(session: Annotated[AsyncSession, Depends(get_session)]) -> ReadinessResponse:
    """Verify the database, packaged migrations and catalogue before promoting a release."""
    try:
        async with asyncio.timeout(5):
            head = migration_head()
            versions = list(await session.scalars(text("SELECT version_num FROM alembic_version")))
            food = await session.scalar(select(CatalogueFood.id).limit(1))
        if versions != [head] or food is None:
            raise HTTPException(503, detail={"code": "database_not_ready"})
    except (SQLAlchemyError, OSError):
        raise HTTPException(503, detail={"code": "database_unavailable"}) from None
    return ReadinessResponse(
        status="ready", service="fuellayer-backend", revision=settings.release_sha, migration=head
    )
