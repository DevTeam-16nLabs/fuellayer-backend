from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Ingredient(StrictModel):
    id: str | None = None
    name: str = Field(default="", max_length=300)
    raw: str = Field(default="", max_length=2000)
    quantity: float | None = Field(default=None, ge=0, le=1_000_000)
    unit: str | None = Field(default=None, max_length=60)
    amount_kind: Literal["numeric", "range", "as_needed", "missing"] = "missing"
    note: str = Field(default="", max_length=1000)
    section: str | None = Field(default=None, max_length=200)
    evidence: str | None = Field(default=None, max_length=2000)
    origin: Literal["source", "user"] = "user"
    food_id: str | None = Field(default=None, max_length=100)
    grams: float | None = Field(default=None, gt=0, le=1_000_000)


class Step(StrictModel):
    id: str | None = None
    text: str = Field(default="", max_length=5000)
    section: str | None = Field(default=None, max_length=200)
    evidence: str | None = Field(default=None, max_length=5000)
    origin: Literal["source", "user"] = "user"


class Nutrients(StrictModel):
    calories_kcal: float | None = Field(default=None, ge=0, le=1_000_000)
    protein_g: float | None = Field(default=None, ge=0, le=1_000_000)
    carbohydrates_g: float | None = Field(default=None, ge=0, le=1_000_000)
    fat_g: float | None = Field(default=None, ge=0, le=1_000_000)


class Nutrition(StrictModel):
    values: Nutrients = Field(default_factory=Nutrients)
    basis: Literal["per_serving", "whole_recipe", "unknown"] = "unknown"
    evidence: str | None = Field(default=None, max_length=4000)
    valid: bool = True


class RecipeFields(StrictModel):
    title: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    servings: float | None = Field(default=None, gt=0, le=10000)
    yield_text: str | None = Field(default=None, max_length=300)
    prep_minutes: float | None = Field(default=None, ge=0, le=100000)
    cook_minutes: float | None = Field(default=None, ge=0, le=100000)
    total_minutes: float | None = Field(default=None, ge=0, le=100000)
    source_url: str | None = Field(default=None, max_length=2048)
    publisher: str | None = Field(default=None, max_length=300)
    author: str | None = Field(default=None, max_length=300)
    author_url: str | None = Field(default=None, max_length=2048)
    dietary_patterns: list[str] = Field(default_factory=list, max_length=10)
    allergens: list[str] = Field(default_factory=list, max_length=20)
    compatibility_reviewed: bool = False


class RecipeData(StrictModel):
    fields: RecipeFields = Field(default_factory=RecipeFields)
    ingredients: list[Ingredient] = Field(default_factory=list, max_length=200)
    steps: list[Step] = Field(default_factory=list, max_length=100)
    source_nutrition: Nutrition = Field(default_factory=Nutrition)
    manual_nutrition: Nutrition = Field(default_factory=Nutrition)
    nutrition_choice: Literal["source", "calculated", "manual"] = "source"

    @model_validator(mode="after")
    def bounded(self) -> "RecipeData":
        if len(self.model_dump_json()) > 150_000:
            raise ValueError("Recipe is too large; shorten the text.")
        return self


class ImportInput(StrictModel):
    kind: Literal["url", "text", "manual"]
    url: str | None = Field(default=None, max_length=2048)
    text: str | None = Field(default=None, max_length=50000)
    source_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def content(self) -> "ImportInput":
        if self.kind == "url" and (not self.url or not self.url.strip()):
            raise ValueError("Enter a recipe link.")
        if self.kind == "text" and (not self.text or not self.text.strip()):
            raise ValueError("Paste the recipe text.")
        return self


class Start(StrictModel):
    input: ImportInput


class Correction(RecipeData):
    expected_version: int = Field(ge=1)
    confirm_compatibility: bool = False


class Save(StrictModel):
    expected_version: int = Field(ge=1)
    status: Literal["draft", "reviewed"]
    acknowledged_issue_ids: list[str] = Field(default_factory=list, max_length=300)


class Version(StrictModel):
    expected_version: int = Field(ge=1)


class Selection(Version):
    candidate_id: str = Field(max_length=100)


class Fallback(Version):
    input: ImportInput
