import re
from datetime import date
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Number = Annotated[float, Field(ge=0, le=20000, allow_inf_nan=False, strict=True)]
Amount = Annotated[float, Field(gt=0, le=10000, allow_inf_nan=False, strict=True)]
Macro = Annotated[float, Field(ge=0, le=5000, allow_inf_nan=False, strict=True)]
Identifier = Annotated[str, Field(min_length=1, max_length=160)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Macros(StrictModel):
    protein_g: Macro
    carbohydrates_g: Macro
    fat_g: Macro


class Nutrients(StrictModel):
    calories_kcal: Number | None
    protein_g: Macro | None
    carbohydrates_g: Macro | None
    fat_g: Macro | None


class Food(StrictModel):
    id: Identifier
    source: Literal["ciqual", "personal", "diary", "recipe"]
    source_version: Annotated[str, Field(max_length=200)]
    source_url: Annotated[str, Field(max_length=2048)]
    name_fr: Annotated[str, Field(min_length=1, max_length=500)]
    name_en: Annotated[str, Field(min_length=1, max_length=500)]
    reference_amount: Amount
    reference_unit: Literal["g", "ml", "portion"]
    unit_amount: Amount | None = None
    nutrients: Nutrients
    nutrient_notes: dict[
        Annotated[str, Field(max_length=64)], Annotated[str, Field(max_length=200)]
    ] = Field(max_length=10)


class Snapshot(StrictModel):
    food: Food
    amount: Amount

    @model_validator(mode="after")
    def validate_scaled(self) -> Self:
        for name, value in self.food.nutrients.model_dump().items():
            if value is not None and value * self.amount / self.food.reference_amount > (
                20000 if name == "calories_kcal" else 5000
            ):
                raise ValueError("Nutrition for the amount eaten exceeds the allowed limit.")
        return self


class Entry(StrictModel):
    id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=500)]
    slot: Literal["Breakfast", "Lunch", "Dinner", "Snack"]
    calories: Number
    macros: Macros | None
    servings: Amount
    mealId: Identifier | None = None
    foodSnapshot: Snapshot | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A food name is required.")
        return value


def calendar_date(value: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use a calendar date in YYYY-MM-DD format.")
    return date.fromisoformat(value)


class Mutation(StrictModel):
    action: Literal["put", "delete", "restore", "migrate"]
    entry_id: Identifier
    diary_date: str
    expected_revision: Annotated[int, Field(ge=0, strict=True)]
    entry: Entry | None = None
    timezone: Annotated[str, Field(max_length=100)] | None = None
    offset_minutes: Annotated[int, Field(ge=-840, le=840, strict=True)] | None = None

    @field_validator("diary_date")
    @classmethod
    def valid_date(cls, value: str) -> str:
        calendar_date(value)
        return value

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("Use a valid IANA timezone.") from exc
        return value

    @model_validator(mode="after")
    def valid_action(self) -> Self:
        if self.action in ("put", "migrate") and self.entry is None:
            raise ValueError("This action requires an entry snapshot.")
        if self.entry and self.entry.id != self.entry_id:
            raise ValueError("Entry IDs must match.")
        if self.action == "delete" and self.entry is not None:
            raise ValueError("Deletion does not replace nutrition.")
        return self
