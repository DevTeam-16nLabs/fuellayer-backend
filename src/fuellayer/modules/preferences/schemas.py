from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    changes: dict[str, Any]


class SaveRequest(PreviewRequest):
    fingerprint: str = Field(min_length=64, max_length=64)
