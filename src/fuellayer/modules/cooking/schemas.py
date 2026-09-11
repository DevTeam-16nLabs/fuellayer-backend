import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

Amount = Annotated[float, Field(gt=0, le=1000000, allow_inf_nan=False, strict=True)]
Portions = Annotated[float, Field(ge=1, le=20, allow_inf_nan=False, strict=True)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Start(Strict):
    request_id: uuid.UUID
    recipe_id: str = Field(min_length=1, max_length=100)
    recipe_version: int | None = Field(default=None, ge=1)
    portions: Portions
    source_yield: Amount | None = None
    acknowledge_draft: StrictBool = False


class Allocation(Strict):
    lot_id: uuid.UUID
    expected_version: int = Field(ge=1, strict=True)
    quantity: Amount
    unit: str = Field(min_length=1, max_length=40)


class IngredientUse(Strict):
    ingredient_id: str = Field(min_length=1, max_length=100)
    skip: StrictBool = False
    allocations: list[Allocation] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=500)


class Preview(Strict):
    uses: list[IngredientUse] = Field(max_length=200)


class Command(Strict):
    request_id: uuid.UUID
    expected_version: int = Field(ge=1, strict=True)
    action: Literal[
        "progress",
        "review",
        "resume",
        "timer_start",
        "timer_pause",
        "timer_resume",
        "timer_restart",
        "timer_cancel",
        "complete",
        "abandon",
        "undo",
    ]
    current_step: str | None = Field(default=None, max_length=100)
    completed_steps: list[str] | None = Field(default=None, max_length=200)
    uses: list[IngredientUse] | None = Field(default=None, max_length=200)
    early_finish: StrictBool = False
    timer_id: uuid.UUID | None = None
    label: str = Field(default="Timer", min_length=1, max_length=120)
    duration_seconds: int | None = Field(default=None, strict=True, ge=1, le=86400)
    started_at: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    candidate_id: str | None = Field(default=None, max_length=100)
    stock_action: Literal["confirm", "skip"] | None = None
    fingerprint: str | None = Field(default=None, max_length=64)
    stop_timers: StrictBool = False
