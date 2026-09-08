from copy import deepcopy
from datetime import date

import httpx
import pytest
from fastapi import HTTPException, Request
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import Base, get_session
from fuellayer.main import create_app
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.onboarding.models import GroceryListRecord, StarterPlanRecord, User
from fuellayer.modules.recipes.service import build_library


def test_recipe_matches_use_explicit_identity_never_infer_sufficient_quantities() -> None:
    public = build_library()
    assert public.inventory_status == "unavailable"
    assert not any(item.at_home_ingredient_names for item in public.items)
    library = build_library(ingredient_keys={"chickpeas", "not-a-catalogue-ingredient"})
    assert library.inventory_status == "available"
    recipe = next(item for item in library.items if item.id == "lunch-quinoa-chickpea")
    assert recipe.at_home_ingredient_names == ["chickpeas"]
    assert not any(
        item.at_home_ingredient_names
        for item in build_library(ingredient_keys={"pois chiches"}).items
    )
    assert not any(
        item.at_home_ingredient_names for item in build_library(ingredient_keys=set()).items
    )


@pytest.mark.asyncio
async def test_inventory_is_private_persistent_read_only_and_preserves_unknowns_and_lots() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _) -> None:  # type: ignore[no-untyped-def]
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    plan = {"schema_version": 2, "days": []}
    grocery = [{"aisle": "Pantry", "items": [{"name": "chickpeas", "quantity": 180, "unit": "g"}]}]
    async with factory() as session:
        alice = User(
            clerk_subject="kitchen-alice",
            starter_plan=StarterPlanRecord(preview_payload=deepcopy(plan), content_version="test"),
            grocery_list=GroceryListRecord(grouped_items=deepcopy(grocery), content_version="test"),
        )
        bob = User(clerk_subject="kitchen-bob")
        session.add_all([alice, bob])
        await session.flush()
        alice_id, bob_id = alice.id, bob.id
        session.add_all(
            [
                KitchenItem(
                    user_id=alice_id,
                    name="Pois chiches",
                    ingredient_key="chickpeas",
                    quantity=None,
                    unit=None,
                    location="pantry",
                    date_on=date(2026, 9, 6),
                ),
                KitchenItem(
                    user_id=alice_id,
                    name="Pois chiches",
                    ingredient_key="chickpeas",
                    quantity=200,
                    unit="g",
                    location="freezer",
                    date_on=None,
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

    async def sessions():  # type: ignore[no-untyped-def]
        async with factory() as session:
            yield session

    app.dependency_overrides[require_auth_subject] = auth
    app.dependency_overrides[get_session] = sessions
    path = "/api/v1/kitchen/inventory"
    a, b = {"x-test-user": "kitchen-alice"}, {"x-test-user": "kitchen-bob"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, headers={"x-test-user": "missing"})).status_code == 404
        response = await client.get(path, headers=a)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        data = response.json()
        assert data["persistence"] == "account"
        assert len(data["items"]) == 2
        unknown = next(item for item in data["items"] if item["location"] == "pantry")
        assert unknown["quantity"] is None and unknown["unit"] is None
        assert unknown["date_on"] == "2026-09-06"  # Never deleted just because its date passed.
        assert "user_id" not in unknown and "ingredient_key" not in unknown
        assert (await client.get(path, headers=a)).json() == data  # fresh session, same stock
        assert (await client.get(path, headers=b, params={"user_id": str(alice_id)})).json()[
            "items"
        ] == []
        assert (
            len(
                (await client.get(path, headers=a, params={"user_id": str(bob_id)})).json()["items"]
            )
            == 2
        )
        for method in ["POST", "PATCH", "DELETE"]:
            assert (await client.request(method, path, headers=a, json={})).status_code == 405
        for headers, expected in [(a, ["chickpeas"]), (b, [])]:
            library = (await client.get("/api/v1/recipes", headers=headers)).json()
            assert library["inventory_status"] == "available"
            recipe = next(
                item for item in library["items"] if item["id"] == "lunch-quinoa-chickpea"
            )
            assert recipe["at_home_ingredient_names"] == expected
        await client.get("/api/v1/recipes/catalogue/lunch-quinoa-chickpea")
        await client.put(
            "/api/v1/recipes/lunch-quinoa-chickpea/saved", headers=a, json={"saved": True}
        )
        assert (await client.get(path, headers=a)).json() == data  # recipes never consume stock
    async with factory() as session:
        assert (await session.scalar(select(StarterPlanRecord))).preview_payload == plan
        assert (await session.scalar(select(GroceryListRecord))).grouped_items == grocery
        await session.delete(await session.get(User, alice_id))
        await session.commit()
        assert not list(await session.scalars(select(KitchenItem)))
    await engine.dispose()
