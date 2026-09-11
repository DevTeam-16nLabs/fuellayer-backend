from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool

from fuellayer.modules.onboarding.schemas import AllergenCode, DietaryPattern


class RecipeNutrition(BaseModel):
    calories_kcal: float | None = Field(ge=0)
    status: Literal["known", "estimated", "missing"]
    reference_servings: float | None = Field(default=1, gt=0)


class RecipeSummary(BaseModel):
    id: str
    name: str
    ingredient_names: list[str]
    prep_minutes: float | None
    nutrition: RecipeNutrition
    source: Literal["fuellayer_catalogue", "user_import"] = "fuellayer_catalogue"
    status: Literal["draft", "reviewed"] | None = None
    version: int | None = None
    source_attribution: dict[str, Any] | None = None
    nutrition_origin: str | None = None
    capabilities: dict[str, Any] | None = None
    issues: list[dict[str, str]] = Field(default_factory=list)
    steps_status: Literal["missing", "available"] = "missing"
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


class RecipeStep(BaseModel):
    id: str
    text: str
    section: str | None = None


class RecipeDetail(RecipeSummary):
    steps: list[RecipeStep] = Field(default_factory=list)
    description: str
    slot: str
    ingredients: list[RecipeIngredient]
    ingredients_reference_servings: float | None = Field(default=1, gt=0)
    macros: dict[Literal["protein_g", "carbohydrates_g", "fat_g"], NutrientValue]
