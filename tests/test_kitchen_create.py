import uuid
from collections.abc import AsyncIterator
from datetime import date

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import Base, get_session
from fuellayer.main import create_app
from fuellayer.modules.foods.models import CatalogueFood
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.onboarding.models import GroceryListRecord, StarterPlanRecord, User


@pytest_asyncio.fixture
async def kitchen_client() -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                User(
                    clerk_subject="alice",
                    starter_plan=StarterPlanRecord(
                        preview_payload={"unchanged": True}, content_version="test"
                    ),
                    grocery_list=GroceryListRecord(grouped_items=[], content_version="test"),
                ),
                User(clerk_subject="bob"),
                CatalogueFood(
                    id="ciqual:test",
                    name_fr="Yaourt nature",
                    name_en="Yogurt",
                    search_text="yogurt",
                    source="ciqual",
                    source_version="2025",
                    source_url="",
                    nutrients={},
                    nutrient_notes={},
                ),
            ]
        )
        await session.commit()
    app = create_app()

    async def auth(request: Request) -> AuthSubject:
        subject = request.headers.get("x-test-user")
        if not subject:
            raise HTTPException(401)
        return AuthSubject(subject)

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[require_auth_subject] = auth
    app.dependency_overrides[get_session] = sessions
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        yield client
    async with factory() as session:
        plan = await session.scalar(select(StarterPlanRecord))
        grocery = await session.scalar(select(GroceryListRecord))
        assert plan is not None and plan.preview_payload == {"unchanged": True}
        assert grocery is not None and grocery.grouped_items == []
        assert all(
            item.ingredient_key is None for item in await session.scalars(select(KitchenItem))
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_is_authenticated_private_and_name_only(
    kitchen_client: httpx.AsyncClient,
) -> None:
    client = kitchen_client
    payload = {"request_id": str(uuid.uuid4()), "name": "  Tomates du marché  "}
    assert (await client.post("/api/v1/kitchen/items", json=payload)).status_code == 401
    assert (
        await client.post("/api/v1/kitchen/items", json=payload, headers={"x-test-user": "missing"})
    ).status_code == 404
    a = {"x-test-user": "alice"}
    response = await client.post("/api/v1/kitchen/items", json=payload, headers=a)
    assert response.status_code == 201
    assert response.headers["cache-control"] == "private, no-store"
    item = response.json()
    assert item["name"] == "Tomates du marché"
    for key in ["quantity", "unit", "date_on", "storage_date", "food_id"]:
        assert item[key] is None
    assert item["location"] == "unassigned"
    assert not {"user_id", "request_id", "request_hash", "ingredient_key"}.intersection(item)
    assert (await client.get("/api/v1/kitchen/inventory", headers=a)).json()["items"] == [item]
    assert (await client.get("/api/v1/kitchen/inventory", headers={"x-test-user": "bob"})).json()[
        "items"
    ] == []


@pytest.mark.asyncio
async def test_retry_conflict_lots_and_account_scope(kitchen_client: httpx.AsyncClient) -> None:
    client = kitchen_client
    payload = {
        "request_id": str(uuid.uuid4()),
        "name": "Yaourt nature",
        "food_id": "ciqual:test",
        "quantity": 2,
        "unit": "pots",
        "location": "fridge",
        "storage_date": "2026-09-01",
    }
    a, b = {"x-test-user": "alice"}, {"x-test-user": "bob"}
    first = await client.post("/api/v1/kitchen/items", headers=a, json=payload)
    assert first.status_code == 201
    assert first.json()["date_on"] is None
    assert first.json()["storage_date"] == "2026-09-01"
    assert (
        await client.post("/api/v1/kitchen/items", headers=a, json=payload)
    ).json() == first.json()
    assert (
        await client.post("/api/v1/kitchen/items", headers=a, json={**payload, "quantity": 3})
    ).status_code == 409
    second = await client.post(
        "/api/v1/kitchen/items", headers=a, json={**payload, "request_id": str(uuid.uuid4())}
    )
    assert second.json()["id"] != first.json()["id"]
    # The same request ID on a different account cannot reveal/replay Alice's item.
    bob = await client.post("/api/v1/kitchen/items", headers=b, json=payload)
    assert bob.status_code == 201 and bob.json()["id"] != first.json()["id"]
    assert len((await client.get("/api/v1/kitchen/inventory", headers=a)).json()["items"]) == 2
    assert len((await client.get("/api/v1/kitchen/inventory", headers=b)).json()["items"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"name": " "},
        {"name": "x" * 201},
        {"quantity": 0, "unit": "g"},
        {"quantity": -1, "unit": "g"},
        {"quantity": 2},
        {"unit": "g"},
        {"quantity": 2, "unit": " "},
        {"quantity": 2, "unit": "x" * 41},
        {"quantity": "Infinity", "unit": "g"},
        {"quantity": "NaN", "unit": "g"},
        {"storage_date": "2026-02-30"},
        {"storage_date": "2999-01-01"},
        {"location": "unknown"},
        {"food_id": "unknown"},
        {"food_id": "ciqual:test", "name": "Unrelated name"},
        {"user_id": str(uuid.uuid4())},
        {"ingredient_key": "chickpeas"},
        {"date_on": date.today().isoformat()},
        {"request_id": "invalid"},
    ],
)
@pytest.mark.asyncio
async def test_invalid_input_cannot_write_stock(
    kitchen_client: httpx.AsyncClient, change: dict[str, object]
) -> None:
    response = await kitchen_client.post(
        "/api/v1/kitchen/items",
        headers={"x-test-user": "alice"},
        json={"request_id": str(uuid.uuid4()), "name": "Test", **change},
    )
    assert response.status_code == 422, response.text
    stock = await kitchen_client.get("/api/v1/kitchen/inventory", headers={"x-test-user": "alice"})
    assert stock.json()["items"] == []
