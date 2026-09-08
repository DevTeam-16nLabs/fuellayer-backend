import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KitchenItemView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    quantity: float | None = Field(allow_inf_nan=False, gt=0)
    unit: str | None
    location: Literal["fridge", "freezer", "pantry", "unassigned"]
    date_on: date | None


class KitchenInventory(BaseModel):
    items: list[KitchenItemView]
    persistence: Literal["account"] = "account"
