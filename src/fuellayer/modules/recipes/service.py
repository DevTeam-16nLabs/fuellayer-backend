from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.onboarding.catalog import MEAL_CATALOG
from fuellayer.modules.onboarding.models import FoodPreference, User
from fuellayer.modules.recipes.models import RecipeBookmark
from fuellayer.modules.recipes.schemas import (
    BookmarkResponse,
    NutrientValue,
    RecipeDetail,
    RecipeIngredient,
    RecipeLibrary,
    RecipeNutrition,
    RecipeSummary,
)


def build_library(
    preferences: FoodPreference | None = None,
    plan: dict[str, Any] | None = None,
    saved_ids: set[str] | None = None,
    ingredient_keys: set[str] | None = None,
) -> RecipeLibrary:
    # Existing V1/V2 plans predate recipe IDs. Names are exact catalogue names,
    # never fuzzy matches; only the catalogue ID is used to persist bookmarks.
    payload = plan or {}
    meals = list(payload.get("meals", []))
    for day in payload.get("days", []):
        meals.extend(day.get("meals", []))
    names = {meal.get("name") for meal in meals}
    recipes = []
    for meal in MEAL_CATALOG:
        compatibility: Literal["matches", "conflict", "unknown"] = "unknown"
        if preferences is not None:
            compatibility = (
                "matches"
                if preferences.dietary_pattern in meal.patterns
                and not set(preferences.allergens).intersection(meal.allergens)
                else "conflict"
            )
        recipes.append(
            RecipeSummary(
                id=meal.id,
                name=meal.name,
                ingredient_names=[ingredient.name for ingredient in meal.ingredients],
                prep_minutes=meal.prep_minutes,
                # These are catalogue estimates for ONE base serving, never the
                # personalised meal values or the total for a shared household.
                nutrition=RecipeNutrition(calories_kcal=meal.calories_kcal, status="estimated"),
                dietary_patterns=sorted(meal.patterns),
                allergens=sorted(meal.allergens),
                compatibility=compatibility,
                in_plan=meal.name in names,
                saved=meal.id in (saved_ids or set()),
                at_home_ingredient_names=[
                    ingredient.name
                    for ingredient in meal.ingredients
                    if ingredient.name in (ingredient_keys or set())
                ],
            )
        )
    return RecipeLibrary(
        items=recipes,
        inventory_status="available" if ingredient_keys is not None else "unavailable",
    )


async def get_user(session: AsyncSession, subject: str, *, lock: bool = False) -> User:
    query = (
        select(User)
        .where(User.clerk_subject == subject)
        .options(selectinload(User.food_preferences), selectinload(User.starter_plan))
    )
    if lock:
        query = query.with_for_update()
    user = await session.scalar(query)
    if user is None:
        raise HTTPException(
            404, detail={"code": "profile_missing", "message": "Profile not found."}
        )
    return user


def build_detail(recipe_id: str) -> RecipeDetail:
    meal = next((item for item in MEAL_CATALOG if item.id == recipe_id), None)
    if meal is None:
        raise HTTPException(404, detail={"code": "recipe_missing", "message": "Recipe not found."})
    summary = next(item for item in build_library().items if item.id == recipe_id)
    return RecipeDetail(
        **summary.model_dump(),
        description=meal.description,
        slot=meal.slot,
        ingredients=[
            RecipeIngredient(name=item.name, quantity=item.quantity, unit=item.unit)
            for item in meal.ingredients
        ],
        macros={
            "protein_g": NutrientValue(value=meal.protein_g, status="estimated"),
            "carbohydrates_g": NutrientValue(value=meal.carbohydrates_g, status="estimated"),
            "fat_g": NutrientValue(value=meal.fat_g, status="estimated"),
        },
    )


async def get_library(session: AsyncSession, subject: str) -> RecipeLibrary:
    user = await get_user(session, subject)
    saved = set(
        await session.scalars(
            select(RecipeBookmark.recipe_id).where(RecipeBookmark.user_id == user.id)
        )
    )
    return build_library(
        user.food_preferences,
        user.starter_plan.preview_payload if user.starter_plan else None,
        saved,
        set(
            await session.scalars(
                select(KitchenItem.ingredient_key).where(
                    KitchenItem.user_id == user.id, KitchenItem.ingredient_key.is_not(None)
                )
            )
        ),
    )


async def set_bookmark(
    session: AsyncSession, subject: str, recipe_id: str, saved: bool
) -> BookmarkResponse:
    if recipe_id not in {meal.id for meal in MEAL_CATALOG}:
        raise HTTPException(404, detail={"code": "recipe_missing", "message": "Recipe not found."})
    # Serialize changes per account. PUT is idempotent even on a network retry;
    # no client-supplied account ID and no writes to plans, stocks or diaries.
    user = await get_user(session, subject, lock=True)
    bookmark = await session.get(RecipeBookmark, (user.id, recipe_id))
    if saved and bookmark is None:
        session.add(RecipeBookmark(user_id=user.id, recipe_id=recipe_id))
    elif not saved and bookmark is not None:
        await session.delete(bookmark)
    await session.commit()
    return BookmarkResponse(id=recipe_id, saved=saved)
