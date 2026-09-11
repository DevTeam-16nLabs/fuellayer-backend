import copy
import uuid

import pytest
from sqlalchemy import select

from fuellayer.modules.courses.models import CoursesLedger
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.onboarding.models import User
from fuellayer.modules.onboarding.schemas import StarterPlanPreviewV2
from test_plan_replacements import BODY, HEADERS, TARGET
from test_plan_replacements import setup as setup

PATH = "/api/v1/plan/portions"
DRAFT = {**TARGET, "personal_portions": 1.5, "total_portions": 4, "audience": "shared"}


async def preview(client, body=None):
    response = await client.post(PATH + "/preview", headers=HEADERS, json=body or DRAFT)
    assert response.status_code == 200, response.text
    return response.json()


async def save(client, body=None):
    body = body or DRAFT
    p = await preview(client, body)
    command = {**body, "fingerprint": p["fingerprint"], "request_id": str(uuid.uuid4())}
    response = await client.post(PATH + "/confirm", headers=HEADERS, json=command)
    assert response.status_code == 200, response.text
    return response.json(), command


@pytest.mark.asyncio
async def test_context_derives_standard_total_instead_of_people(setup):
    client, _ = setup
    r = await client.post(PATH + "/context", headers=HEADERS, json=TARGET)
    assert r.status_code == 200
    assert r.json()["personal_portions"] == 1.5
    assert r.json()["total_portions"] == 2.5  # personal 1.5 + 1 standard for another diner
    assert r.json()["audience"] == "shared"


@pytest.mark.asyncio
async def test_total_changes_no_nutrition_no_preview_writes_idempotent_undo(setup):
    client, factory = setup
    async with factory() as session:
        user = await session.scalar(select(User).where(User.clerk_subject == "alice"))
        await session.refresh(user, ["starter_plan"])
        before = copy.deepcopy(user.starter_plan.preview_payload)
        stock = [(i.id, i.quantity, i.version) for i in await session.scalars(select(KitchenItem))]
    p = await preview(client)
    assert p["day_before"] == p["day_after"]
    assert p["current"]["calories_kcal"] == p["replacement"]["calories_kcal"]
    assert p["replacement"]["portions"] == 1.5
    assert p["total_before"] == 2.5 and p["total_after"] == 4
    for a, b in zip(p["ingredients_before"], p["ingredients_after"], strict=True):
        assert b["quantity"] == pytest.approx(a["quantity"] * 4 / 2.5)
    async with factory() as session:
        user = await session.scalar(select(User).where(User.clerk_subject == "alice"))
        await session.refresh(user, ["starter_plan"])
        assert user.starter_plan.preview_payload == before
        assert await session.get(CoursesLedger, user.id) is None
    result, command = await save(client)
    assert result["replacement"]["kind"] == "portions"
    updated = result["plan"]
    assert updated["input_hash"] == before["input_hash"]
    assert updated["days"][0] == before["days"][0]
    assert updated["days"][2:] == before["days"][2:]
    assert updated["grocery"]["fresh_refreshes"] == before["grocery"]["fresh_refreshes"]
    # Public bootstrap schema must retain explicit portions after reload.
    meal = StarterPlanPreviewV2.model_validate(updated).model_dump()["days"][1]["meals"][0]
    assert meal["total_portions"] == 4 and meal["portion_audience"] == "shared"
    repeated = await client.post(PATH + "/confirm", headers=HEADERS, json=command)
    assert repeated.json() == result
    undo = await client.post(
        PATH + "/undo", headers=HEADERS, json={"replacement_id": command["request_id"]}
    )
    assert undo.status_code == 200, undo.text
    assert undo.json()["plan"]["days"] == before["days"]
    assert undo.json()["plan"]["grocery"] == before["grocery"]
    async with factory() as session:
        assert [
            (i.id, i.quantity, i.version) for i in await session.scalars(select(KitchenItem))
        ] == stock


@pytest.mark.asyncio
async def test_fractional_personal_only_changes_nutrition_not_shopping(setup):
    client, _ = setup
    result, _ = await save(client)
    draft = {**DRAFT, "plan_revision": result["plan"]["content_version"], "personal_portions": 0.5}
    p = await preview(client, draft)
    assert p["replacement"]["calories_kcal"] == round(p["current"]["calories_kcal"] / 3)
    assert p["ingredients_before"] == p["ingredients_after"]
    assert p["shopping_changes"] == []
    r, _ = await save(client, draft)
    assert r["plan"]["grocery"] == result["plan"]["grocery"]


@pytest.mark.asyncio
async def test_personal_batch_and_mode_only_change_are_independent(setup):
    client, _ = setup
    r, _ = await save(client, {**DRAFT, "audience": "personal", "total_portions": 2.5})
    assert r["plan"]["days"][1]["meals"][0]["total_portions"] == 2.5
    assert StarterPlanPreviewV2.model_validate(r["plan"]).days[1].meals[0].audience == "just_me"
    p = await preview(
        client, {**DRAFT, "plan_revision": r["plan"]["content_version"], "total_portions": 2.5}
    )
    assert p["audience_before"] == "personal" and p["audience_after"] == "shared"
    assert p["ingredients_before"] == p["ingredients_after"]
    assert p["shopping_changes"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        {"personal_portions": 0},
        {"total_portions": 21},
        {"personal_portions": 1.255},
        {"total_portions": 1},
        {"total_portions": 1.5},
        {"audience": "mixed"},
        {"personal_portions": "NaN"},
        {"total_portions": "Infinity"},
    ],
)
async def test_invalid_quantities_rejected(setup, values):
    client, _ = setup
    r = await client.post(PATH + "/preview", headers=HEADERS, json={**DRAFT, **values})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_missing_basis_blocks(setup):
    client, factory = setup
    async with factory() as session:
        user = await session.scalar(select(User).where(User.clerk_subject == "alice"))
        await session.refresh(user, ["starter_plan", "food_preferences"])
        plan = copy.deepcopy(user.starter_plan.preview_payload)
        plan["days"][1]["meals"][0]["ingredients"][0]["quantity"] = None
        user.starter_plan.preview_payload = plan
        await session.commit()
    r = await client.post(PATH + "/context", headers=HEADERS, json=TARGET)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "portion_unknown"


@pytest.mark.asyncio
async def test_protected_purchases_and_overrides_survive_with_requirement_diff(setup):
    client, factory = setup
    await client.get("/api/v1/courses", headers=HEADERS)
    async with factory() as session:
        user = await session.scalar(select(User).where(User.clerk_subject == "alice"))
        ledger = await session.get(CoursesLedger, user.id)
        data = copy.deepcopy(ledger.data)
        data["items"][0]["status"] = "purchased"
        data["items"][1]["overridden"] = True
        data["items"][1]["quantity"] = 123
        protected = copy.deepcopy(data["items"][:2])
        ledger.data = data
        await session.commit()
    p = await preview(client)
    kept = [c for c in p["shopping_changes"] if c["kind"] == "kept"]
    assert len(kept) >= 2 and all("required_quantity" in c for c in kept)
    await save(client)
    async with factory() as session:
        user = await session.scalar(select(User).where(User.clerk_subject == "alice"))
        ledger = await session.get(CoursesLedger, user.id)
        for item in protected:
            assert next(i for i in ledger.data["items"] if i["id"] == item["id"]) == item


@pytest.mark.asyncio
async def test_stale_preview_and_cross_operation_undo_conflict(setup):
    client, _ = setup
    old = await preview(client)
    saved, command = await save(client)
    stale = await client.post(
        PATH + "/confirm",
        headers=HEADERS,
        json={**DRAFT, "fingerprint": old["fingerprint"], "request_id": str(uuid.uuid4())},
    )
    assert stale.status_code == 409
    # Replacement must preserve the new explicit fractional batch.
    replacement = {**BODY, "plan_revision": saved["plan"]["content_version"]}
    p = await client.post("/api/v1/plan/replacements/preview", headers=HEADERS, json=replacement)
    assert p.status_code == 200, p.text
    new = await client.post(
        "/api/v1/plan/replacements/confirm",
        headers=HEADERS,
        json={
            **replacement,
            "fingerprint": p.json()["fingerprint"],
            "request_id": str(uuid.uuid4()),
        },
    )
    assert new.status_code == 200
    assert new.json()["plan"]["days"][1]["meals"][0]["total_portions"] == 4
    undo = await client.post(
        PATH + "/undo", headers=HEADERS, json={"replacement_id": command["request_id"]}
    )
    assert undo.status_code == 409
    bob = await client.get(PATH + "/" + command["request_id"], headers={"x-test-user": "bob"})
    assert bob.json()["replacement"] is None
    assert (await client.post(PATH + "/context", json=TARGET)).status_code == 401


@pytest.mark.asyncio
async def test_known_allergy_conflict_cannot_be_saved(setup):
    client, factory = setup
    async with factory() as session:
        user = await session.scalar(select(User).where(User.clerk_subject == "alice"))
        await session.refresh(user, ["food_preferences"])
        user.food_preferences.allergens = ["fish"]
        await session.commit()
    response = await client.post(PATH + "/preview", headers=HEADERS, json=DRAFT)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "allergy_conflict"


@pytest.mark.asyncio
async def test_unchanged_confirmation_does_not_write(setup):
    client, _ = setup
    draft = {**DRAFT, "total_portions": 2.5}
    p = await preview(client, draft)
    result = await client.post(
        PATH + "/confirm",
        headers=HEADERS,
        json={**draft, "fingerprint": p["fingerprint"], "request_id": str(uuid.uuid4())},
    )
    assert result.status_code == 422
    assert result.json()["detail"]["code"] == "unchanged"
