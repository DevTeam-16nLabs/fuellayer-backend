import uuid

import httpx
import pytest

from test_kitchen_create import kitchen_client as kitchen_client

A = {"x-test-user": "alice"}
B = {"x-test-user": "bob"}


async def add(client: httpx.AsyncClient) -> dict[str, object]:
    result = await client.post(
        "/api/v1/kitchen/items",
        headers=A,
        json={
            "request_id": str(uuid.uuid4()),
            "name": "Yaourt nature",
            "quantity": 2,
            "unit": "pots",
            "location": "fridge",
            "storage_date": "2026-09-01",
            "food_id": "ciqual:test",
        },
    )
    assert result.status_code == 201
    return result.json()  # type: ignore[no-any-return]


def edit(item: dict[str, object], **changes: object) -> dict[str, object]:
    return {key: item[key] for key in ["name", "quantity", "unit", "location", "storage_date"]} | {
        "request_id": str(uuid.uuid4()),
        "expected_version": item["version"],
        **changes,
    }


@pytest.mark.asyncio
async def test_edit_lot_identity_null_zero_and_retry(kitchen_client: httpx.AsyncClient) -> None:
    c = kitchen_client
    first, second = await add(c), await add(c)
    url = f"/api/v1/kitchen/items/{first['id']}"
    payload = edit(first, quantity=0, unit=None, storage_date=None, location="pantry")
    saved = await c.patch(url, headers=A, json=payload)
    assert saved.status_code == 200, saved.text
    item = saved.json()
    assert item["quantity"] == 0 and item["unit"] is None and item["storage_date"] is None
    assert item["id"] == first["id"] and item["version"] == 2 and item["status"] == "active"
    assert item["food_id"] == first["food_id"]
    assert saved.headers["cache-control"] == "private, no-store"
    assert (await c.patch(url, headers=A, json=payload)).json() == item
    assert (await c.patch(url, headers=A, json={**payload, "quantity": 9})).status_code == 409
    assert (await c.patch(url, headers=A, json=edit(first, quantity=9))).status_code == 409
    result = await c.patch(
        url, headers=A, json=edit(item, quantity=None, unit="pots", name="Mon yaourt")
    )
    assert result.status_code == 200
    assert result.json()["quantity"] is None and result.json()["unit"] == "pots"
    assert result.json()["food_id"] is None
    stock = (await c.get("/api/v1/kitchen/inventory", headers=A)).json()["items"]
    assert len(stock) == 2
    assert next(row for row in stock if row["id"] == second["id"]) == second


@pytest.mark.parametrize("status", ["finished", "removed"])
@pytest.mark.asyncio
async def test_retire_undo_retry_and_stale_undo(
    kitchen_client: httpx.AsyncClient, status: str
) -> None:
    c = kitchen_client
    original, other = await add(c), await add(c)
    url = f"/api/v1/kitchen/items/{original['id']}/status"
    payload = {"request_id": str(uuid.uuid4()), "expected_version": 1, "status": status}
    response = await c.post(url, headers=A, json=payload)
    assert response.status_code == 200
    retired = response.json()
    assert retired["status"] == status and retired["quantity"] == 2
    assert (await c.post(url, headers=A, json=payload)).json() == retired
    stock = (await c.get("/api/v1/kitchen/inventory", headers=A)).json()["items"]
    assert stock == [other]
    assert (
        await c.patch(url.removesuffix("/status"), headers=A, json=edit(retired))
    ).status_code == 409
    undo = {"request_id": str(uuid.uuid4()), "expected_version": 2, "status": "active"}
    restored = await c.post(url, headers=A, json=undo)
    assert restored.status_code == 200
    assert restored.json() == {**original, "version": 3}
    assert (await c.post(url, headers=A, json=undo)).json() == restored.json()
    again = await c.post(
        url, headers=A, json={**payload, "request_id": str(uuid.uuid4()), "expected_version": 3}
    )
    assert again.status_code == 200
    assert (
        await c.post(url, headers=A, json={**undo, "request_id": str(uuid.uuid4())})
    ).status_code == 409
    # An old request must never redo a past operation, including after undo.
    await c.post(url, headers=A, json=undo)
    assert (await c.get("/api/v1/kitchen/inventory", headers=A)).json()["items"] == [other]


@pytest.mark.asyncio
async def test_edit_and_status_require_owner(kitchen_client: httpx.AsyncClient) -> None:
    c = kitchen_client
    item = await add(c)
    url = f"/api/v1/kitchen/items/{item['id']}"
    for headers, code in [({}, 401), (B, 404)]:
        assert (await c.patch(url, headers=headers, json=edit(item))).status_code == code
        assert (
            await c.post(
                url + "/status",
                headers=headers,
                json={
                    "request_id": str(uuid.uuid4()),
                    "expected_version": 1,
                    "status": "removed",
                },
            )
        ).status_code == code
    assert (await c.get("/api/v1/kitchen/inventory", headers=A)).json()["items"] == [item]


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": -1},
        {"quantity": "Infinity"},
        {"quantity": "NaN"},
        {"name": " "},
        {"name": "x" * 201},
        {"unit": ""},
        {"unit": "x" * 41},
        {"location": "missing"},
        {"storage_date": "2026-02-30"},
        {"storage_date": "2999-01-01"},
        {"date_on": "2026-01-01"},
        {"food_id": "ciqual:test"},
        {"user_id": str(uuid.uuid4())},
        {"expected_version": 0},
    ],
)
@pytest.mark.asyncio
async def test_edit_validation(
    kitchen_client: httpx.AsyncClient, change: dict[str, object]
) -> None:
    item = await add(kitchen_client)
    result = await kitchen_client.patch(
        f"/api/v1/kitchen/items/{item['id']}", headers=A, json=edit(item, **change)
    )
    assert result.status_code == 422, result.text
    assert (await kitchen_client.get("/api/v1/kitchen/inventory", headers=A)).json()["items"] == [
        item
    ]


@pytest.mark.asyncio
async def test_status_request_validation_and_cross_lot_request_reuse(
    kitchen_client: httpx.AsyncClient,
) -> None:
    c = kitchen_client
    first, second = await add(c), await add(c)
    url = f"/api/v1/kitchen/items/{first['id']}/status"
    for change in [{"status": "deleted"}, {"expected_version": 0}, {"user_id": str(uuid.uuid4())}]:
        response = await c.post(
            url,
            headers=A,
            json={
                "request_id": str(uuid.uuid4()),
                "expected_version": 1,
                "status": "removed",
                **change,
            },
        )
        assert response.status_code == 422
    payload = {"request_id": str(uuid.uuid4()), "expected_version": 1, "status": "removed"}
    assert (await c.post(url, headers=A, json=payload)).status_code == 200
    assert (
        await c.post(f"/api/v1/kitchen/items/{second['id']}/status", headers=A, json=payload)
    ).status_code == 409
    assert (await c.get("/api/v1/kitchen/inventory", headers=A)).json()["items"] == [second]


@pytest.mark.asyncio
async def test_only_active_nonzero_or_unknown_lots_count_as_at_home() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from fuellayer.core.database import Base
    from fuellayer.modules.kitchen.models import KitchenItem
    from fuellayer.modules.onboarding.models import User
    from fuellayer.modules.recipes.service import get_library

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = User(clerk_subject="recipe-stock-test")
        session.add(user)
        await session.flush()
        item = KitchenItem(
            user_id=user.id,
            name="Pois chiches",
            ingredient_key="chickpeas",
            quantity=None,
            unit=None,
            location="pantry",
        )
        session.add(item)
        await session.commit()
        for status, quantity, expected in [
            ("active", None, True),
            ("active", 0, False),
            ("active", 2, True),
            ("finished", 2, False),
            ("removed", None, False),
            ("active", None, True),
        ]:
            item.status, item.quantity = status, quantity
            await session.commit()
            library = await get_library(session, user.clerk_subject)
            recipe = next(row for row in library.items if row.id == "lunch-quinoa-chickpea")
            assert bool(recipe.at_home_ingredient_names) is expected
    await engine.dispose()
