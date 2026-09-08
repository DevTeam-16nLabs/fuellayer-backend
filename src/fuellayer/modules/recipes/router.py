from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.onboarding.rate_limit import SlidingWindowRateLimiter
from fuellayer.modules.recipes.schemas import (
    BookmarkResponse,
    BookmarkUpdate,
    RecipeDetail,
    RecipeLibrary,
)
from fuellayer.modules.recipes.service import build_detail, build_library, get_library, set_bookmark

router = APIRouter(prefix="/recipes", tags=["recipes"])
Session = Annotated[AsyncSession, Depends(get_session)]
Auth = Annotated[AuthSubject, Depends(require_auth_subject)]
limiter = SlidingWindowRateLimiter(120)


@router.get("/catalogue", response_model=RecipeLibrary)
async def catalogue(request: Request) -> RecipeLibrary:
    # Public, non-personal catalogue, also used by the existing development session.
    if not limiter.allow(request.client.host if request.client else "unknown"):
        raise HTTPException(429, detail="Please wait before trying again.")
    return build_library()


@router.get("", response_model=RecipeLibrary)
async def library(auth: Auth, session: Session, response: Response) -> RecipeLibrary:
    response.headers["Cache-Control"] = "private, no-store"
    return await get_library(session, auth.subject)


@router.get("/catalogue/{recipe_id}", response_model=RecipeDetail)
async def detail(recipe_id: str, request: Request) -> RecipeDetail:
    # Base catalogue quantities only. Account-specific compatibility/bookmarks
    # remain in the authenticated library; no personal plan is exposed here.
    if not limiter.allow(request.client.host if request.client else "unknown"):
        raise HTTPException(429, detail="Please wait before trying again.")
    return build_detail(recipe_id)


@router.put("/{recipe_id}/saved", response_model=BookmarkResponse)
async def bookmark(
    recipe_id: str, payload: BookmarkUpdate, auth: Auth, session: Session
) -> BookmarkResponse:
    return await set_bookmark(session, auth.subject, recipe_id, payload.saved)
