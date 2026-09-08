"""Idempotent catalogue import; updates source records without touching diary snapshots."""

import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from fuellayer.core.database import engine, session_factory
from fuellayer.modules.foods.models import CatalogueFood
from fuellayer.modules.foods.schemas import Food
from fuellayer.modules.foods.service import normalize_query


async def main() -> None:
    path = Path(__file__).resolve().parents[1] / "data" / "ciqual-2025.json"
    foods = [Food.model_validate(row) for row in json.loads(path.read_text())]
    async with session_factory() as session:
        existing = {food.id: food for food in (await session.scalars(select(CatalogueFood)))}
        for food in foods:
            record = existing.get(food.id)
            if record is None:
                record = CatalogueFood(id=food.id)
                session.add(record)
            for field in ("name_fr", "name_en", "source", "source_version", "source_url"):
                setattr(record, field, getattr(food, field))
            record.search_text = normalize_query(f"{food.name_fr} {food.name_en}")
            record.nutrients = food.nutrients.model_dump()
            record.nutrient_notes = food.nutrient_notes
        await session.commit()
    await engine.dispose()
    print(f"Imported {len(foods)} Ciqual foods (FR/EN).")


if __name__ == "__main__":
    asyncio.run(main())
