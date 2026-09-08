import re
import unicodedata

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.modules.foods.models import CatalogueFood
from fuellayer.modules.foods.schemas import Food, FoodSearchResponse


def normalize_query(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("œ", "oe"))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", value))


async def search_foods(
    session: AsyncSession, query: str, locale: str, offset: int, limit: int
) -> FoodSearchResponse:
    normalized = normalize_query(query)
    tokens = normalized.split()
    if not tokens:
        return FoodSearchResponse(items=[])
    statement = select(CatalogueFood)
    for token in tokens:
        statement = statement.where(CatalogueFood.search_text.contains(token, autoescape=True))
    name = CatalogueFood.name_fr if locale == "fr" else CatalogueFood.name_en
    # Rank accented and unaccented queries identically, just as filtering does.
    # Preserve commas here so a food's base name outranks ingredient mentions.
    ranked_name = func.lower(name)
    for accented in "àáâäãåçèéêëìíîïñòóôöõùúûüýÿœ":
        ranked_name = func.replace(ranked_name, accented, normalize_query(accented))
    # Deterministic pagination, favor the full phrase before scattered token matches.
    statement = (
        statement.order_by(
            case((CatalogueFood.nutrients["calories_kcal"].as_float().is_(None), 1), else_=0),
            case((ranked_name.startswith(normalized + ",", autoescape=True), 0), else_=1),
            case((CatalogueFood.search_text.contains(normalized, autoescape=True), 0), else_=1),
            func.length(name),
            name,
            CatalogueFood.id,
        )
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list((await session.scalars(statement)).all())
    return FoodSearchResponse(
        items=[Food.model_validate(row) for row in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )
