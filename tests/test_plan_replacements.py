import copy
import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import Base, get_session
from fuellayer.main import create_app
from fuellayer.modules.courses.models import CoursesLedger
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.onboarding.models import (
    FoodPreference,
    GroceryListRecord,
    StarterPlanRecord,
    User,
)
from fuellayer.modules.plan.models import MealReplacement
from fuellayer.modules.plan.service import CATALOG, converted, coverage, group, scaled
from fuellayer.modules.recipes.models import RecipeBookmark

PATH = "/api/v1/plan/replacements"
HEADERS = {"x-test-user": "alice"}
TARGET = {
    "start_date": "2026-09-07",
    "date": "2026-09-08",
    "meal_id": "day-2-dinner",
    "plan_revision": "v1",
}
BODY = {**TARGET, "recipe_id": "lunch-quinoa-chickpea"}


def preference() -> FoodPreference:
    return FoodPreference(
        dietary_pattern="none",
        allergens=["soy"],
        meals_per_day=3,
        include_breakfast=True,
        include_snacks=False,
        cooking_time="30_min",
        servings=2,
        shopping_cadence="monthly",
    )


def plan_fixture() -> dict[str, Any]:
    user = User(food_preferences=preference())
    seed = {
        "id": "seed",
        "name": "seed",
        "slot": "Dinner",
        "portions": 1.5,
        "audience": "shared",
        "servings": 2,
    }
    meal = scaled(seed, "dinner-salmon-quinoa", user)
    rows = {
        str(i): {
            "name": item.name,
            "quantity": item.quantity * 2.5 * 7,
            "unit": item.unit,
            "aisle": item.aisle,
        }
        for i, item in enumerate(CATALOG["dinner-salmon-quinoa"].ingredients)
    }
    return {
        "schema_version": 2,
        "status": "ready",
        "content_version": "v1",
        "input_hash": "hash",
        "engine_version": "v2",
        "confidence": "standard",
        "daily_energy_kcal": 2100,
        "macros": {"protein_g": 100, "carbohydrates_g": 200, "fat_g": 50},
        "days": [{"day": i, "meals": [{**meal, "id": f"day-{i}-dinner"}]} for i in range(1, 8)],
        "grocery": {
            "horizon_days": 28,
            "main_trip": group(rows),
            "fresh_refreshes": [{"day_offset": 7, "label": "Week 2", "sections": group(rows)}],
        },
        "explanations": [],
        "warnings": [],
    }


@pytest_asyncio.fixture
async def setup():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for name in ("alice", "bob"):
            session.add(
                User(
                    clerk_subject=name,
                    onboarding_status="completed",
                    food_preferences=preference(),
                    starter_plan=StarterPlanRecord(
                        preview_payload=plan_fixture(), content_version="v1"
                    ),
                    grocery_list=GroceryListRecord(
                        grouped_items=plan_fixture()["grocery"]["main_trip"], content_version="v1"
                    ),
                )
            )
        await session.flush()
        alice = await session.scalar(select(User).where(User.clerk_subject == "alice"))
        session.add_all(
            [
                RecipeBookmark(user_id=alice.id, recipe_id="lunch-tofu-rice"),
                RecipeBookmark(user_id=alice.id, recipe_id="lunch-quinoa-chickpea"),
                KitchenItem(
                    user_id=alice.id,
                    name="quinoa",
                    ingredient_key="quinoa",
                    quantity=0.12,
                    unit="kg",
                    location="pantry",
                ),
                KitchenItem(
                    user_id=alice.id,
                    name="cucumber",
                    ingredient_key="cucumber",
                    quantity=None,
                    unit=None,
                    location="fridge",
                ),
                KitchenItem(
                    user_id=alice.id,
                    name="quinoa",
                    ingredient_key="quinoa",
                    quantity=10000,
                    unit="g",
                    status="removed",
                    location="pantry",
                ),
            ]
        )
        await session.commit()
    app = create_app()

    async def auth(request: Request) -> AuthSubject:
        if not request.headers.get("x-test-user"):
            raise HTTPException(401)
        return AuthSubject(request.headers["x-test-user"])

    async def sessions():
        async with factory() as session:
            yield session

    app.dependency_overrides[require_auth_subject] = auth
    app.dependency_overrides[get_session] = sessions
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        yield client, factory
    await engine.dispose()


async def preview(client: httpx.AsyncClient, body: dict[str, Any] | None = None) -> dict[str, Any]:
    r = await client.post(PATH + "/preview", headers=HEADERS, json=body or BODY)
    assert r.status_code == 200, r.text
    return r.json()


async def commit(client: httpx.AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    p = await preview(client)
    body = {**BODY, "fingerprint": p["fingerprint"], "request_id": str(uuid.uuid4())}
    r = await client.post(PATH + "/confirm", headers=HEADERS, json=body)
    assert r.status_code == 200, r.text
    return r.json(), body


@pytest.mark.asyncio
async def test_preview_no_writes_personal_scale_household_unknown_and_saved_exclusions(setup):
    client, factory = setup
    assert (await client.post(PATH + "/context", json=TARGET)).status_code == 401
    context = await client.post(PATH + "/context", headers=HEADERS, json=TARGET)
    assert context.headers["cache-control"] == "private, no-store"
    assert context.json()["saved_excluded_count"] == 1
    assert "lunch-tofu-rice" not in [i["id"] for i in context.json()["items"]]
    p = await preview(client)
    assert p["replacement"]["calories_kcal"] == 885  # 590 x 1.5, not x household count
    assert p["replacement"]["portions"] == 1.5 and p["cooking_servings"] == 2
    quinoa = next(i for i in p["ingredients_after"] if i["name"] == "quinoa")
    assert quinoa["quantity"] == 225 and quinoa["at_home"] == 120 and quinoa["shortage"] == 105
    cucumber = next(i for i in p["ingredients_after"] if i["name"] == "cucumber")
    assert cucumber["present"] and cucumber["shortage"] is None
    assert p["fingerprint"] == (await preview(client))["fingerprint"]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(CoursesLedger)) == 0
        assert await session.scalar(select(func.count()).select_from(MealReplacement)) == 0
        plans = await session.scalars(select(StarterPlanRecord))
        assert all(p.preview_payload == plan_fixture() for p in plans)


@pytest.mark.asyncio
async def test_commit_one_occurrence_roundtrip_undo_retry_and_no_inventory_mutation(setup):
    client, factory = setup
    result, body = await commit(client)
    plan = result["plan"]
    assert plan["days"][1]["meals"][0]["id"] == TARGET["meal_id"]
    assert plan["days"][1]["meals"][0]["name"] == "Lemon chickpea quinoa"
    assert plan["days"][0] == plan_fixture()["days"][0]
    assert plan["grocery"]["fresh_refreshes"] == plan_fixture()["grocery"]["fresh_refreshes"]
    assert plan["start_date"] == "2026-09-07" and plan["input_hash"] == "hash"
    retry = await client.post(PATH + "/confirm", headers=HEADERS, json=body)
    assert retry.json() == result
    assert len((await client.get(PATH, headers=HEADERS)).json()) == 1
    assert (
        await client.get(PATH + "/" + body["request_id"], headers={"x-test-user": "bob"})
    ).json()["replacement"] is None
    undone = await client.post(
        PATH + "/undo", headers=HEADERS, json={"replacement_id": body["request_id"]}
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["plan"]["days"] == plan_fixture()["days"]
    replay = await client.post(PATH + "/confirm", headers=HEADERS, json=body)
    assert replay.json()["replacement"]["status"] == "undone"
    assert replay.json()["plan"]["days"] == plan_fixture()["days"]
    async with factory() as session:
        lots = list(await session.scalars(select(KitchenItem)))
        assert sorted(i.quantity for i in lots if i.quantity is not None) == [0.12, 10000]
        assert all(i.version == 1 for i in lots)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["stock", "preference", "shopping"])
async def test_preview_invalidated_on_concurrent_changes(setup, change):
    client, factory = setup
    p = await preview(client)
    async with factory() as session:
        user = await session.scalar(select(User).where(User.clerk_subject == "alice"))
        if change == "stock":
            item = await session.scalar(select(KitchenItem).where(KitchenItem.user_id == user.id))
            item.quantity, item.version = 5, item.version + 1
        elif change == "preference":
            pref = await session.scalar(
                select(FoodPreference).where(FoodPreference.user_id == user.id)
            )
            pref.allergens = ["soy", "sesame"]
        else:
            session.add(
                CoursesLedger(
                    user_id=user.id,
                    version=1,
                    data={"items": [], "receipts": [], "transfers": [], "plan_version": "v1"},
                )
            )
        await session.commit()
    r = await client.post(
        PATH + "/confirm",
        headers=HEADERS,
        json={**BODY, "fingerprint": p["fingerprint"], "request_id": str(uuid.uuid4())},
    )
    assert r.status_code == 409
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(MealReplacement)) == 0


@pytest.mark.asyncio
async def test_known_allergen_excluded_even_direct_request_and_no_foreign_undo(setup):
    client, _ = setup
    r = await client.post(
        PATH + "/preview", headers=HEADERS, json={**BODY, "recipe_id": "lunch-tofu-rice"}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "allergy_conflict"
    _, body = await commit(client)
    r = await client.post(
        PATH + "/undo", headers={"x-test-user": "bob"}, json={"replacement_id": body["request_id"]}
    )
    assert r.status_code == 404
    r = await client.post(PATH + "/confirm", headers=HEADERS, json={**body, "recipe_id": "bad"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_protected_rows_preserved_and_purchasing_added_item_blocks_undo(setup):
    client, factory = setup
    s = (await client.get("/api/v1/courses", headers=HEADERS)).json()
    async with factory() as session:
        ledger = await session.scalar(select(CoursesLedger))
        data = copy.deepcopy(ledger.data)
        data["items"][0]["status"] = "purchased"
        data["items"][1]["overridden"] = True
        data["items"][1]["quantity"] = 99
        data["items"].append(
            {
                "id": "manual",
                "name": "cucumber",
                "quantity": 2,
                "unit": "item",
                "aisle": "Produce",
                "status": "to_buy",
                "source_key": None,
                "overridden": True,
                "lot_id": None,
            }
        )
        protected = copy.deepcopy([data["items"][0], data["items"][1], data["items"][-1]])
        ledger.data = data
        ledger.version += 1
        await session.commit()
    p = await preview(client)
    assert not any(c["kind"] == "added" and c["name"] == "cucumber" for c in p["shopping_changes"])
    _, body = await commit(client)
    s = (await client.get("/api/v1/courses", headers=HEADERS)).json()
    for row in protected:
        assert next(i for i in s["items"] if i["id"] == row["id"]) == row
    added = next(c for c in p["shopping_changes"] if c["kind"] == "added")
    r = await client.post(
        "/api/v1/courses/actions",
        headers=HEADERS,
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": s["version"],
            "action": "check",
            "item_id": added["id"],
            "checked": True,
        },
    )
    assert r.status_code == 200
    r = await client.post(
        PATH + "/undo", headers=HEADERS, json={"replacement_id": body["request_id"]}
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "undo_conflict"


def test_quantity_conversion_never_guesses_can_sizes_or_mass_volume():
    assert converted(1, "kg", "g") == 1000
    assert converted(2, "cans", "g") is None
    assert converted(100, "ml", "g") is None
    assert coverage([{"name": "x", "quantity": 2, "unit": "g"}], [])[0]["present"] is False
