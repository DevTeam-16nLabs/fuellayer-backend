import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fuellayer.main import app
from fuellayer.modules.foods import router as food_router
from fuellayer.modules.foods.models import CatalogueFood
from fuellayer.modules.foods.schemas import Food, FoodSearchResponse
from fuellayer.modules.foods.service import normalize_query, search_foods


def catalogue() -> list[Food]:
    return [
        Food.model_validate(row)
        for row in json.loads((Path(__file__).parents[1] / "data" / "ciqual-2025.json").read_text())
    ]


def test_catalogue_keeps_source_facts_and_unknown_values() -> None:
    foods = catalogue()
    assert len(foods) == len({food.id for food in foods}) == 3484
    assert all(food.name_fr and food.name_en for food in foods)
    assert all(food.reference_amount == 100 and food.reference_unit == "g" for food in foods)
    censored = [food for food in foods if food.nutrient_notes]
    assert censored
    for food in censored:
        for nutrient in food.nutrient_notes:
            assert getattr(food.nutrients, nutrient) is None
    rice = next(food for food in foods if food.id == "ciqual:9104")
    assert rice.nutrients.calories_kcal == 155
    assert rice.source_version == "2025"


@pytest.mark.asyncio
async def test_search_bilingual_accents_cooking_state_and_pagination() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(CatalogueFood.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        for food in catalogue():
            session.add(
                CatalogueFood(
                    **food.model_dump(exclude={"reference_amount", "reference_unit"}),
                    search_text=normalize_query(food.name_fr + " " + food.name_en),
                )
            )
        await session.commit()
        for query, locale in [
            ("banane", "fr"),
            ("banana", "en"),
            ("epinard", "fr"),
            ("œuf", "fr"),
            ("chicken raw", "en"),
            ("poulet cru", "fr"),
        ]:
            result = await search_foods(session, query, locale, 0, 25)
            assert result.items, query
            for food in result.items:
                haystack = normalize_query(food.name_fr + " " + food.name_en)
                assert all(token in haystack for token in normalize_query(query).split())
        page1 = await search_foods(session, "riz", "fr", 0, 5)
        assert page1.next_offset == 5
        page2 = await search_foods(session, "riz", "fr", 5, 5)
        assert not {f.id for f in page1.items} & {f.id for f in page2.items}
        accented = await search_foods(session, "épinard", "fr", 0, 5)
        plain = await search_foods(session, "epinard", "fr", 0, 5)
        assert [food.id for food in accented.items] == [food.id for food in plain.items]
        assert not (await search_foods(session, "%___", "fr", 0, 25)).items
        assert not (await search_foods(session, "qwertyuiopzz", "fr", 0, 25)).items
    await engine.dispose()


def test_public_api_validates_search_and_returns_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search(*args: object) -> FoodSearchResponse:
        return FoodSearchResponse(items=catalogue()[:1])

    monkeypatch.setattr(food_router, "search_foods", fake_search)
    with TestClient(app) as client:
        result = client.get("/api/v1/foods/search?q=riz&locale=fr")
        assert result.status_code == 200
        assert result.json()["items"][0]["reference_unit"] == "g"
        assert "Anses" in result.json()["attribution"]
        for query in ["q=a", "q=riz&limit=1000", "q=riz&offset=-1", "q=riz&locale=de"]:
            assert client.get("/api/v1/foods/search?" + query).status_code == 422
