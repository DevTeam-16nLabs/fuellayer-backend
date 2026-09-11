"""Resolve an owned import or catalogue recipe without an account-global cache."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.modules.onboarding.catalog import MEAL_CATALOG, CatalogIngredient, PurchaseKind
from fuellayer.modules.onboarding.models import User
from fuellayer.modules.onboarding.schemas import AllergenCode, DietaryPattern
from fuellayer.modules.recipe_imports import service
from fuellayer.modules.recipes.service import build_detail


@dataclass(frozen=True)
class ResolvedRecipe:
    id: str
    name: str
    description: str
    calories_kcal: float
    protein_g: float
    carbohydrates_g: float
    fat_g: float
    prep_minutes: float | None
    patterns: frozenset[DietaryPattern]
    allergens: frozenset[AllergenCode]
    ingredients: tuple[CatalogIngredient, ...]
    snapshot: dict[str, Any]
    nutrition_status: str = "estimated"


def catalogue(recipe_id: str) -> ResolvedRecipe | None:
    meal = next((m for m in MEAL_CATALOG if m.id == recipe_id), None)
    if not meal:
        return None
    # Preserve the catalogue hash used by already-saved portion adjustments.
    snapshot = build_detail(recipe_id).model_dump(
        exclude={
            "status",
            "version",
            "source_attribution",
            "nutrition_origin",
            "capabilities",
            "issues",
        }
    )
    snapshot["prep_minutes"] = meal.prep_minutes
    return ResolvedRecipe(
        meal.id,
        meal.name,
        meal.description,
        meal.calories_kcal,
        meal.protein_g,
        meal.carbohydrates_g,
        meal.fat_g,
        meal.prep_minutes,
        meal.patterns,
        meal.allergens,
        meal.ingredients,
        snapshot,
    )


def from_snapshot(user: User, snapshot: dict[str, Any]) -> ResolvedRecipe:
    if snapshot.get("owner") != str(user.id) or snapshot.get("status") != "reviewed":
        service.fail("recipe_missing", "The recipe snapshot could not be verified.", 404)
    data = snapshot["data"]
    basis = data["ingredients_reference_servings"]
    return ResolvedRecipe(
        data["id"],
        data["name"],
        data["description"],
        data["nutrition"]["calories_kcal"],
        data["macros"]["protein_g"]["value"],
        data["macros"]["carbohydrates_g"]["value"],
        data["macros"]["fat_g"]["value"],
        data["prep_minutes"],
        frozenset(DietaryPattern(v) for v in data["dietary_patterns"]),
        frozenset(AllergenCode(v) for v in data["allergens"]),
        tuple(
            CatalogIngredient(
                i["name"], i["quantity"] / basis, i["unit"], "Other", PurchaseKind.LONG_LIFE
            )
            for i in data["ingredients"]
        ),
        snapshot,
        data["nutrition"]["status"],
    )


def for_meal(user: User, meal: dict[str, Any]) -> ResolvedRecipe | None:
    recipe_id = meal.get("recipe_id") or ""
    if recipe_id.startswith("import:"):
        snapshot = meal.get("recipe_snapshot")
        if not snapshot or snapshot.get("data", {}).get("id") != recipe_id:
            return None
        return from_snapshot(user, snapshot)
    return catalogue(recipe_id) or next(
        (catalogue(m.id) for m in MEAL_CATALOG if m.name == meal["name"]), None
    )


async def resolve(session: AsyncSession, user: User, recipe_id: str) -> ResolvedRecipe:
    if not recipe_id.startswith("import:"):
        recipe = catalogue(recipe_id)
        if not recipe:
            service.fail("recipe_missing", "Recipe not found.", 404)
        return recipe
    record = await service.owned(session, user, recipe_id)
    data = await service.detail(session, user, record)
    if not data["capabilities"]["replace"]["allowed"]:
        service.fail("recipe_incomplete", " ".join(data["capabilities"]["replace"]["reasons"]), 422)
    return from_snapshot(
        user, {"owner": str(user.id), "status": "reviewed", "version": record.version, "data": data}
    )
