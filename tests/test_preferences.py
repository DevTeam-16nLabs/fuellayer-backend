import copy
import uuid

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import Base, get_session
from fuellayer.main import create_app
from fuellayer.modules.cooking.models import CookingSession
from fuellayer.modules.courses.models import CoursesLedger
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.onboarding.models import (
    BodyMeasurement,
    FoodPreference,
    NutritionTargetHistory,
    PreferenceReceipt,
    StarterPlanRecord,
    User,
)
from fuellayer.modules.onboarding.service import complete_onboarding
from fuellayer.modules.preferences.constraints import allergy_matches
from fuellayer.modules.preferences.service import INGREDIENTS
from fuellayer.modules.recipes.service import build_library
from test_onboarding import answers, answers_v2

PATH = "/api/v1/me/preferences"
ALICE = {"x-test-user": "alice"}


@pytest_asyncio.fixture
async def setup(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/preferences.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        for name in ["alice", "bob"]:
            await complete_onboarding(db, name, name + "-onboarding", answers_v2())
        await complete_onboarding(db, "legacy", "legacy-onboarding", answers())
        alice = await db.scalar(select(User).where(User.clerk_subject == "alice"))
        db.add(
            KitchenItem(
                user_id=alice.id,
                name="rice",
                ingredient_key="rice",
                quantity=200,
                unit="g",
                location="pantry",
                version=3,
            )
        )
        db.add(
            CoursesLedger(
                user_id=alice.id,
                version=2,
                data={
                    "items": [{"id": "bought", "purchased": True}],
                    "receipts": [{"id": "receipt"}],
                    "transfers": [{"id": "transfer"}],
                },
            )
        )
        db.add(
            CookingSession(
                user_id=alice.id,
                status="in_progress",
                snapshot={"untouched": True},
                state={"timers": [{"id": "timer", "status": "running"}], "current_step": "step-2"},
            )
        )
        await db.commit()
    app = create_app()

    async def auth(request: Request):
        subject = request.headers.get("x-test-user")
        if not subject:
            raise HTTPException(401)
        return AuthSubject(subject)

    async def sessions():
        async with factory() as db:
            yield db

    app.dependency_overrides[require_auth_subject] = auth
    app.dependency_overrides[get_session] = sessions
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, factory
    await engine.dispose()


async def read(client, headers=ALICE):
    r = await client.get(PATH, headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    return r.json()


async def save(client, changes, revision=None, key=None, headers=ALICE):
    if revision is None:
        revision = (await read(client, headers))["revision"]
    body = {"changes": changes, "expected_revision": revision}
    preview = await client.post(PATH + "/preview", headers=headers, json=body)
    assert preview.status_code == 200, preview.text
    body["fingerprint"] = preview.json()["fingerprint"]
    request_headers = {**headers, "Idempotency-Key": key or str(uuid.uuid4())}
    r = await client.patch(PATH, headers=request_headers, json=body)
    assert r.status_code == 200, r.text
    return r.json(), body, request_headers


async def protected(factory):
    from fuellayer.modules.onboarding.models import GroceryListRecord

    async with factory() as db:
        return {
            "plans": [
                (p.content_version, copy.deepcopy(p.preview_payload))
                for p in (await db.scalars(select(StarterPlanRecord))).all()
            ],
            "groceries": [
                (p.content_version, copy.deepcopy(p.grouped_items))
                for p in (await db.scalars(select(GroceryListRecord))).all()
            ],
            "pantry": [
                (p.quantity, p.version, p.status)
                for p in (await db.scalars(select(KitchenItem))).all()
            ],
            "purchases": [
                (p.version, copy.deepcopy(p.data))
                for p in (await db.scalars(select(CoursesLedger))).all()
            ],
            "cooking": [
                (p.version, copy.deepcopy(p.state), copy.deepcopy(p.snapshot))
                for p in (await db.scalars(select(CookingSession))).all()
            ],
        }


@pytest.mark.asyncio
async def test_real_roundtrip_partial_retry_owner_and_no_collateral_changes(setup):
    client, factory = setup
    before = await read(client)
    untouched = await protected(factory)
    saved, body, headers = await save(client, {"profile": {"weight_kg": 82}, "units": "imperial"})
    assert saved["values"]["profile"]["weight_kg"] == 82
    assert saved["values"]["profile"]["height_cm"] == before["values"]["profile"]["height_cm"]
    assert saved["values"]["food"] == before["values"]["food"]
    assert saved["target"] != before["target"]
    assert saved["revision"] == 1
    assert (await read(client)) == saved  # independent DB session, no in-memory response cache
    assert (await client.patch(PATH, json=body, headers=headers)).json() == saved
    assert (await read(client, {"x-test-user": "bob"}))["revision"] == 0
    assert await protected(factory) == untouched
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(PreferenceReceipt)) == 1
        assert await db.scalar(select(func.count()).select_from(BodyMeasurement)) == 4
        assert (
            await db.scalar(select(func.count()).select_from(NutritionTargetHistory)) == 4
        )  # Three prospective baselines + one edit.
    history = (await client.get(PATH + "/target-history", headers=ALICE)).json()["items"]
    assert (
        len(history) == 2
        and history[0]["target"]["daily_energy_kcal"] == before["target"]["daily_energy_kcal"]
    )
    assert (
        len(
            (await client.get(PATH + "/target-history", headers={"x-test-user": "bob"})).json()[
                "items"
            ]
        )
        == 1
    )  # Bob retains only his own prospective baseline.
    reused = {**body, "changes": {"units": "metric"}}
    assert (await client.patch(PATH, json=reused, headers=headers)).json()["detail"][
        "code"
    ] == "request_reused"


@pytest.mark.asyncio
async def test_validation_ownership_and_conflicts(setup):
    client, factory = setup
    assert (await client.get(PATH)).status_code == 401
    assert (await client.get(PATH, headers={"x-test-user": "unknown"})).status_code == 404
    invalid = [
        {"owner": "bob"},
        {"meal_contexts": None},
        {"meal_contexts": 2},
        {"food": {"excluded_ingredients": [{}]}},
        {"household": []},
        {"food": None},
        {"profile": {"user_id": "bob"}},
        {"profile": {"age": 17}},
        {"profile": {"weight_kg": -5}},
        {"profile": {"height_cm": 0}},
        {"profile": {"age": True}},
        {"activity": {"training_days": True}},
        {"units": "stones"},
        {"goal": {"type": "maintain", "detail": "steady"}},
        {"food": {"allergens": ["soy", "soy"]}},
        {"food": {"allergens": ["banana"]}},
        {"food": {"excluded_ingredients": ["unrecognized food"]}},
        {"food": {"snack_slots": [3]}},
        {"food": {"include_breakfast": "false"}},
        {"household": {"household_size": 20}},
        {"shopping": {"preferred_place_ids": ["a", "b", "c", "d"]}},
        {"shopping": {"location_status": "skipped", "preferred_place_ids": ["a"]}},
        {
            "meal_contexts": [
                {
                    "slot": "dinner",
                    "audience": "shared",
                    "shared_days_per_week": 7,
                    "shared_servings": 5,
                    "owner": "bob",
                }
            ]
        },
    ]
    for changes in invalid:
        r = await client.post(
            PATH + "/preview", json={"expected_revision": 0, "changes": changes}, headers=ALICE
        )
        assert r.status_code == 422, (changes, r.text)
    draft = await client.post(
        PATH + "/preview",
        json={"expected_revision": 0, "changes": {"units": "imperial"}},
        headers=ALICE,
    )
    await save(client, {"food": {"cooking_time": "15_min"}})
    conflict = await client.patch(
        PATH,
        json={
            "expected_revision": 0,
            "changes": {"units": "imperial"},
            "fingerprint": draft.json()["fingerprint"],
        },
        headers={**ALICE, "Idempotency-Key": "conflict-request"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current"]["values"]["food"]["cooking_time"] == "15_min"
    assert (await read(client))["values"]["units"] == "metric"


@pytest.mark.asyncio
async def test_allergy_and_exclusion_consumers_and_unknown_meals(setup):
    client, factory = setup
    original = await protected(factory)
    saved, _, _ = await save(
        client,
        {
            "food": {
                "allergens": ["milk", "fish", "peanuts"],
                "excluded_ingredients": [INGREDIENTS[0]],
            }
        },
    )
    assert saved["restriction_review"]["items"]
    assert any("Contains:" in " ".join(i["reasons"]) for i in saved["restriction_review"]["items"])
    async with factory() as db:
        user = await db.scalar(select(User).where(User.clerk_subject == "alice"))
        pref = await db.scalar(select(FoodPreference).where(FoodPreference.user_id == user.id))
        lib = build_library(preferences=pref)
        assert all(r.compatibility == "conflict" for r in lib.items if "fish" in r.allergens)
        plan = await db.scalar(
            select(StarterPlanRecord).where(StarterPlanRecord.user_id == user.id)
        )
        payload = copy.deepcopy(plan.preview_payload)
        payload["days"][0]["meals"][0]["name"] = "Unverified family recipe"
        payload["days"][0]["meals"][0].pop("recipe_id", None)
        plan.preview_payload = payload
        await db.commit()
    assert any(
        i["status"] == "unknown" for i in (await read(client))["restriction_review"]["items"]
    )
    assert (await protected(factory))["pantry"] == original["pantry"]
    next_plan = await client.post(PATH + "/next-plan-preview", headers=ALICE)
    assert next_plan.status_code == 200, next_plan.text
    if next_plan.json()["status"] == "ready":
        for day in next_plan.json()["days"]:
            for meal in day["meals"]:
                assert not any(i["name"] == INGREDIENTS[0] for i in meal["ingredients"])
    assert allergy_matches(["shellfish"], ["crustaceans"]) == ["shellfish"]


@pytest.mark.asyncio
async def test_legacy_missing_details_and_shopping_without_onboarding_reset(setup):
    client, factory = setup
    headers = {"x-test-user": "legacy"}
    before = await read(client, headers)
    assert before["values"]["household"] is None
    saved, _, _ = await save(
        client,
        {
            "shopping": {
                "cadence": "monthly",
                "location_status": "selected",
                "location_source": "manual",
                "area_label": "Dakar",
                "country_code": "SN",
                "preferred_place_ids": ["store-a"],
            }
        },
        headers=headers,
    )
    assert saved["values"]["shopping"]["area_label"] == "Dakar"
    assert saved["values"]["household"] is None
    assert (await read(client, headers))["values"]["shopping"] == saved["values"]["shopping"]


@pytest.mark.asyncio
async def test_changed_plan_requires_fresh_impact_and_units_do_not_change_targets(setup):
    client, factory = setup
    before = await read(client)
    body = {"expected_revision": 0, "changes": {"units": "imperial"}}
    preview = (await client.post(PATH + "/preview", json=body, headers=ALICE)).json()
    async with factory() as db:
        user = await db.scalar(select(User).where(User.clerk_subject == "alice"))
        plan = await db.scalar(
            select(StarterPlanRecord).where(StarterPlanRecord.user_id == user.id)
        )
        plan.content_version = "another-meal-edit"
        await db.commit()
    response = await client.patch(
        PATH,
        json={**body, "fingerprint": preview["fingerprint"]},
        headers={**ALICE, "Idempotency-Key": "stale-plan-edit"},
    )
    assert response.status_code == 409 and response.json()["detail"]["code"] == "preview_changed"
    assert (await read(client))["revision"] == 0
    saved, _, _ = await save(client, {"units": "imperial"})
    assert saved["target"] == before["target"]
    async with factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(NutritionTargetHistory)) == 3
        )  # Units-only edits add no events.


@pytest.mark.asyncio
async def test_saved_cooking_time_filters_future_replacement_choices(setup):
    from fuellayer.modules.plan.schemas import Occurrence
    from fuellayer.modules.plan.service import context
    from fuellayer.modules.preferences.service import owned

    client, factory = setup
    await save(client, {"food": {"cooking_time": "15_min"}})
    async with factory() as db:
        user = await owned(db, "alice")
        plan = user.starter_plan
        first = plan.preview_payload["days"][0]["meals"][0]
        result = await context(
            db,
            "alice",
            Occurrence(
                start_date="2026-09-09",
                date="2026-09-09",
                meal_id=first["id"],
                plan_revision=plan.content_version,
            ),
        )
        assert result["items"]
        assert all(r["prep_minutes"] <= 15 for r in result["items"])


@pytest.mark.asyncio
async def test_nonfinite_numeric_input_returns_validation_error(setup):
    client, _ = setup
    for number in ["NaN", "Infinity", "-Infinity"]:
        response = await client.post(
            PATH + "/preview",
            headers={**ALICE, "content-type": "application/json"},
            content='{ "expected_revision": 0, "changes": { "profile": { "weight_kg": '
            + number
            + " } } }",
        )
        assert response.status_code == 422, response.text
    assert (await read(client))["revision"] == 0
