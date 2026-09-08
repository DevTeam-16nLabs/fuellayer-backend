from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fuellayer.core.database import Base


class CatalogueFood(Base):
    __tablename__ = "catalogue_foods"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name_fr: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32))
    source_version: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text)
    nutrients: Mapped[dict[str, float | None]] = mapped_column(JSON)
    nutrient_notes: Mapped[dict[str, str]] = mapped_column(JSON)
