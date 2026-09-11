import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fuellayer.modules.kitchen.schemas import Location

Category = Literal[
    "Non classé",
    "Fruits & légumes",
    "Produits laitiers",
    "Œufs",
    "Viandes & poissons",
    "Légumineuses",
    "Céréales",
    "Matières grasses",
    "Boissons",
    "Autres aliments",
]


class FoodDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    category: Category = "Non classé"
    location: Location = "unassigned"
    aisle: str = Field(default="Autres rayons", min_length=1, max_length=80)

    @model_validator(mode="after")
    def pair(self) -> "FoodDetails":
        if (self.quantity is None) != (self.unit is None):
            raise ValueError("Indiquez une quantité et une unité, ou laissez les deux inconnues.")
        return self


class TransferLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    food: FoodDetails
    # Explicit user choice, never inferred from a name match.
    existing_lot_id: uuid.UUID | None = None
    new_purchase: bool = False


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID
    expected_version: int = Field(ge=1)
    action: Literal[
        "add",
        "edit",
        "check",
        "remove",
        "restore",
        "transfer",
        "undo_transfer",
        "resolve",
        "ignore",
        "undo_receipt",
        "receipt_duplicate",
        "receipt_distinct",
    ]
    item_id: str | None = None
    receipt_id: str | None = None
    line_id: str | None = None
    food: FoodDetails | None = None
    checked: bool | None = None
    lines: list[TransferLine] = Field(default_factory=list, max_length=100)
    existing_lot_id: uuid.UUID | None = None
    new_purchase: bool = False


class ReceiptUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID
    # Bounded JSON data URL avoids accepting arbitrary URLs or following redirects.
    image: str = Field(max_length=8_000_000, min_length=24)


class ExtractedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw: str = Field(max_length=500)
    kind: Literal["food", "non_food", "tax", "discount", "total", "uncertain"]
    name: str | None = Field(max_length=200)
    identity_confident: bool
    quantity: float | None = Field(gt=0, allow_inf_nan=False)
    unit: str | None = Field(max_length=40)
    quantity_evidence: str | None = Field(max_length=200)
    category: Category
    location: Location
    location_confident: bool
    price: str | None = Field(max_length=40)


class ExtractedReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    readable: bool
    merchant: str | None = Field(max_length=200)
    purchase_date: str | None = Field(max_length=40)
    receipt_number: str | None = Field(max_length=100)
    total: str | None = Field(max_length=40)
    lines: list[ExtractedLine] = Field(max_length=200)
