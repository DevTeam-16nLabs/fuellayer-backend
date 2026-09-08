from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Nutrients(BaseModel):
    calories_kcal: float | None = Field(default=None, ge=0)
    protein_g: float | None = Field(default=None, ge=0)
    carbohydrates_g: float | None = Field(default=None, ge=0)
    fat_g: float | None = Field(default=None, ge=0)


class Food(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name_fr: str
    name_en: str
    source: Literal["ciqual"] = "ciqual"
    source_version: str = "2025"
    source_url: str = "https://doi.org/10.5281/zenodo.17550133"
    reference_amount: float = 100
    reference_unit: Literal["g"] = "g"
    nutrients: Nutrients
    # A censored value (e.g. '< 0.1') is not an exact measured zero.
    nutrient_notes: dict[str, str] = Field(default_factory=dict)


class FoodSearchResponse(BaseModel):
    items: list[Food]
    next_offset: int | None = None
    attribution: str = "Anses. 2025. Ciqual French food composition table."
