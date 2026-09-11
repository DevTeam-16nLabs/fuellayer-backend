import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Occurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    date: date
    meal_id: str = Field(min_length=1, max_length=200)
    plan_revision: str = Field(min_length=1, max_length=64)


class PreviewRequest(Occurrence):
    recipe_id: str = Field(min_length=1, max_length=100)


class ConfirmRequest(PreviewRequest):
    request_id: uuid.UUID
    fingerprint: str = Field(min_length=64, max_length=64)


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replacement_id: uuid.UUID


Portion = Annotated[Decimal, Field(ge=Decimal("0.25"), le=20, decimal_places=2)]


class PortionPreviewRequest(Occurrence):
    personal_portions: Portion
    total_portions: Portion
    audience: Literal["personal", "shared"]

    @model_validator(mode="after")
    def includes_personal(self) -> Self:
        if self.total_portions < self.personal_portions:
            raise ValueError("Total must include your personal portion.")
        if self.audience == "shared" and self.total_portions == self.personal_portions:
            raise ValueError("Add some portions for sharing, or choose Just me.")
        return self


class PortionConfirmRequest(PortionPreviewRequest):
    request_id: uuid.UUID
    fingerprint: str = Field(min_length=64, max_length=64)
