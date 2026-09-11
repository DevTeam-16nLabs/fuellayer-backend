import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Location = Literal["fridge", "freezer", "pantry", "unassigned"]


class KitchenItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: uuid.UUID
    category: str = Field(default="Non classé", min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    quantity: float | None = Field(default=None, allow_inf_nan=False, gt=0)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    location: Location = "unassigned"
    storage_date: date | None = None
    food_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def quantity_pair(self) -> "KitchenItemCreate":
        if (self.quantity is None) != (self.unit is None):
            raise ValueError("Quantity and unit must be supplied together.")
        return self


class KitchenItemView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    category: str = "Non classé"
    quantity: float | None = Field(allow_inf_nan=False, ge=0)
    unit: str | None
    location: Location
    date_on: date | None
    storage_date: date | None
    food_id: str | None
    status: Literal["active", "finished", "removed"] = "active"
    version: int = 1


class KitchenItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: uuid.UUID
    expected_version: int = Field(ge=1)
    category: str = Field(default="Non classé", min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    quantity: float | None = Field(allow_inf_nan=False, ge=0)
    unit: str | None = Field(min_length=1, max_length=40)
    location: Location
    storage_date: date | None


class KitchenItemStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID
    expected_version: int = Field(ge=1)
    status: Literal["active", "finished", "removed"]


class KitchenInventory(BaseModel):
    items: list[KitchenItemView]
    persistence: Literal["account"] = "account"
