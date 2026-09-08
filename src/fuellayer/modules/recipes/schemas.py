from typing import Literal

from pydantic import BaseModel, Field, StrictBool

from fuellayer.modules.onboarding.schemas import AllergenCode, DietaryPattern


class RecipeNutrition(BaseModel):
    calories_kcal: float | None = Field(ge=0)
    status: Literal["known", "estimated", "missing"]
    reference_servings: int = Field(default=1, ge=1)


class RecipeSummary(BaseModel):
    id: str
    name: str
    ingredient_names: list[str]
    prep_minutes: int | None
    nutrition: RecipeNutrition
    source: Literal["fuellayer_catalogue"] = "fuellayer_catalogue"
    steps_status: Literal["missing"] = "missing"
    dietary_patterns: list[DietaryPattern]
    allergens: list[AllergenCode]
    compatibility: Literal["matches", "conflict", "unknown"] = "unknown"
    in_plan: bool = False
    saved: bool = False
    at_home_ingredient_names: list[str] = Field(default_factory=list)


class RecipeLibrary(BaseModel):
    items: list[RecipeSummary]
    inventory_status: Literal["unavailable", "available"] = "unavailable"


class BookmarkUpdate(BaseModel):
    saved: StrictBool


class BookmarkResponse(BaseModel):
    id: str
    saved: bool


class NutrientValue(BaseModel):
    value: float | None = Field(ge=0)
    status: Literal["known", "estimated", "missing"]


class RecipeIngredient(BaseModel):
    name: str
    quantity: float | None = Field(ge=0)
    unit: str


class RecipeDetail(RecipeSummary):
    description: str
    slot: str
    ingredients: list[RecipeIngredient]
    ingredients_reference_servings: int = Field(default=1, ge=1)
    macros: dict[Literal["protein_g", "carbohydrates_g", "fat_g"], NutrientValue]
