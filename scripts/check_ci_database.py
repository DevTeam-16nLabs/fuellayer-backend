"""Exercise real migrations and repeatable catalogue imports on the CI database."""

import asyncio
import json
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from import_foods import main as import_foods
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from fuellayer.core.config import settings
from fuellayer.core.database import engine, session_factory
from fuellayer.modules.foods.models import CatalogueFood


async def snapshot(head: str) -> dict[str, dict[str, Any]]:
    async with session_factory() as db:
        versions = (await db.scalars(text("SELECT version_num FROM alembic_version"))).all()
        assert versions == [head], f"Unexpected migration revision: {versions}"
        rows = (await db.scalars(select(CatalogueFood))).all()
        return {
            row.id: {
                "name_fr": row.name_fr,
                "name_en": row.name_en,
                "search_text": row.search_text,
                "source": row.source,
                "source_version": row.source_version,
                "source_url": row.source_url,
                "nutrients": row.nutrients,
                "nutrient_notes": row.nutrient_notes,
            }
            for row in rows
        }


async def check_catalogue(head: str) -> None:
    try:
        await import_foods()
        first = await snapshot(head)
        source = Path(__file__).resolve().parents[1] / "data" / "ciqual-2025.json"
        expected = json.loads(source.read_text())
        assert set(first) == {row["id"] for row in expected}, "Catalogue IDs differ from the source"
        assert len(first) == len(expected), "Duplicate or missing catalogue entries"
        await import_foods()
        assert await snapshot(head) == first, "Reimporting changed catalogue records"
        print(f"Validated {len(first)} catalogue records and idempotent reimport.")
    finally:
        await engine.dispose()


def main() -> None:
    database = make_url(settings.database_url)
    if (
        settings.environment != "test"
        or database.get_backend_name() != "postgresql"
        or not (database.database or "").startswith("fuellayer_ci")
    ):
        raise SystemExit(
            "Use ENVIRONMENT=test and a disposable PostgreSQL database named fuellayer_ci*."
        )
    config = Config("alembic.ini")
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"Expected one migration head, found {heads}"
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    asyncio.run(check_catalogue(heads[0]))
    print(f"Validated migration chain through {heads[0]} and repeated upgrade.")


if __name__ == "__main__":
    main()
