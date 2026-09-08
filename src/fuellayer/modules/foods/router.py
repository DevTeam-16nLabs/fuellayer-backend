from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.database import get_session
from fuellayer.modules.foods.models import CatalogueFood
from fuellayer.modules.foods.schemas import Food, FoodSearchResponse
from fuellayer.modules.foods.service import search_foods
from fuellayer.modules.onboarding.rate_limit import SlidingWindowRateLimiter

router = APIRouter(prefix="/foods", tags=["foods"])
limiter = SlidingWindowRateLimiter(120)
Session = Annotated[AsyncSession, Depends(get_session)]


def check_rate(request: Request) -> None:
    if not limiter.allow(request.client.host if request.client else "unknown"):
        raise HTTPException(
            429,
            detail={
                "code": "food_search_rate_limited",
                "message": "Please wait before searching again.",
            },
        )


@router.get("/search", response_model=FoodSearchResponse)
async def search(
    request: Request,
    session: Session,
    q: Annotated[str, Query(min_length=2, max_length=120)],
    locale: Literal["fr", "en"] = "fr",
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> FoodSearchResponse:
    check_rate(request)
    return await search_foods(session, q, locale, offset, limit)


@router.get("/{food_id}", response_model=Food)
async def get_food(food_id: str, request: Request, session: Session) -> Food:
    check_rate(request)
    food = await session.get(CatalogueFood, food_id)
    if food is None:
        raise HTTPException(404, detail={"code": "food_not_found", "message": "Food not found."})
    return Food.model_validate(food)
