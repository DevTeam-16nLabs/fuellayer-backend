# ruff: noqa: F811
import uuid
from decimal import Decimal

import pytest

from fuellayer.modules.cooking.domain import convert, timer_candidates
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.recipe_imports.schemas import RecipeData
from fuellayer.modules.recipe_imports.service import create_recipe
from fuellayer.modules.recipes.service import get_user
from test_plan_replacements import HEADERS, setup  # noqa: F401

PATH = "/api/v1/cooking/sessions"


async def seed(factory, **changes):
    async with factory() as db:
        user = await get_user(db, "alice")
        data = RecipeData.model_validate(
            {
                "fields": {"title": "Test salad", "servings": 2},
                "ingredients": [
                    {"name": "quinoa", "quantity": 90, "unit": "g", "amount_kind": "numeric"},
                    {"name": "oil", "quantity": None, "unit": None},
                ],
                "steps": [{"text": "Rinse quinoa."}, {"text": "Cook for 10–15 minutes."}],
                **changes,
            }
        )
        recipe = await create_recipe(db, user.id, data, "manual")
        await db.commit()
        return {
            "recipe_id": f"import:{recipe.id}",
            "recipe_version": recipe.version,
            "portions": 2,
            "acknowledge_draft": True,
            "request_id": str(uuid.uuid4()),
        }


async def start(client, factory):
    body = await seed(factory)
    res = await client.post(PATH, headers=HEADERS, json=body)
    assert res.status_code == 201, res.text
    return res.json(), body


async def command(client, state, action, **data):
    body = {
        "request_id": str(uuid.uuid4()),
        "expected_version": state["version"],
        "action": action,
        **data,
    }
    res = await client.post(f"{PATH}/{state['id']}/actions", headers=HEADERS, json=body)
    return res, body


def test_units_and_literal_timers():
    assert convert(90, "g", "kg") == Decimal("0.09")
    assert convert(1, "ml", "g") is None
    assert convert(1, "tbsp", "ml") is None
    assert convert(2, "items", "pièces") == 2
    rows = timer_candidates(
        [{"id": "a", "text": "Cook at 180 C for 10–15 minutes; rest 30 seconds."}]
    )
    assert [(r["minimum"], r["maximum"]) for r in rows] == [(600, 900), (30, 30)]


@pytest.mark.asyncio
async def test_snapshot_resume_replay_and_ownership(setup):
    client, factory = setup
    state, body = await start(client, factory)
    again = await client.post(PATH, headers=HEADERS, json=body)
    assert again.json()["id"] == state["id"]
    assert (
        await client.post(PATH, headers=HEADERS, json={**body, "portions": 4})
    ).status_code == 409
    assert (
        await client.post(PATH, headers=HEADERS, json={**body, "request_id": str(uuid.uuid4())})
    ).status_code == 409
    assert (
        await client.get(f"{PATH}/{state['id']}", headers={"x-test-user": "bob"})
    ).status_code == 404
    assert (
        await client.post(
            f"{PATH}/{state['id']}/actions",
            headers={"x-test-user": "bob"},
            json={"action": "abandon", "request_id": str(uuid.uuid4()), "expected_version": 1},
        )
    ).status_code == 404
    step = state["snapshot"]["recipe"]["steps"][1]["id"]
    progress, payload = await command(client, state, "progress", current_step=step)
    assert progress.status_code == 200
    assert progress.json()["completed_steps"] == []
    async with factory() as db:
        from fuellayer.modules.recipe_imports import service

        user = await get_user(db, "alice")
        recipe = await service.owned(db, user, body["recipe_id"])
        await db.delete(recipe)
        await db.commit()
    recovered = (await client.get(f"{PATH}/{state['id']}", headers=HEADERS)).json()
    assert recovered["snapshot"]["recipe"]["name"] == "Test salad"
    assert recovered["current_step"] == step
    assert (
        await client.post(f"{PATH}/{state['id']}/actions", headers=HEADERS, json=payload)
    ).json()["version"] == 2
    assert (await command(client, state, "resume"))[0].status_code == 409


@pytest.mark.asyncio
async def test_missing_steps_yield_and_draft(setup):
    client, factory = setup
    body = await seed(factory, steps=[])
    assert (await client.post(PATH, headers=HEADERS, json=body)).status_code == 422
    body = await seed(factory, fields={"title": "No yield", "servings": None})
    assert (await client.post(PATH, headers=HEADERS, json=body)).status_code == 422
    assert (
        await client.post(
            PATH, headers=HEADERS, json={**body, "source_yield": 2, "acknowledge_draft": False}
        )
    ).status_code == 422
    result = await client.post(
        PATH, headers=HEADERS, json={**body, "source_yield": 2, "portions": 1.25}
    )
    assert result.status_code == 201
    assert result.json()["snapshot"]["ingredients"][0]["quantity"] == 56.25
    assert result.json()["snapshot"]["ingredients"][1]["quantity"] is None


@pytest.mark.asyncio
async def test_timers_retries_finish_requires_decision(setup):
    client, factory = setup
    state, _ = await start(client, factory)
    response, payload = await command(
        client,
        state,
        "timer_start",
        timer_id=str(uuid.uuid4()),
        duration_seconds=600,
        label="Quinoa",
    )
    assert response.status_code == 200, response.text
    state = response.json()
    again = (
        await client.post(f"{PATH}/{state['id']}/actions", headers=HEADERS, json=payload)
    ).json()
    assert again["timers"] == state["timers"]
    response, _ = await command(client, state, "review", early_finish=True)
    state = response.json()
    assert (await command(client, state, "complete", stock_action="skip"))[0].status_code == 409
    done, _ = await command(client, state, "complete", stock_action="skip", stop_timers=True)
    assert done.json()["stock"]["status"] == "skipped"
    assert done.json()["timers"][0]["status"] == "cancelled"
    assert (await command(client, done.json(), "progress", current_step=state["current_step"]))[
        0
    ].status_code == 409


async def usage(client, state):
    pantry = (await client.get(f"{PATH}/{state['id']}/pantry", headers=HEADERS)).json()["items"]
    lot = next(i for i in pantry if i["name"] == "quinoa")
    ingredients = state["snapshot"]["ingredients"]
    return [
        {
            "ingredient_id": ingredients[0]["id"],
            "allocations": [
                {
                    "lot_id": lot["id"],
                    "expected_version": lot["version"],
                    "quantity": 90,
                    "unit": "g",
                }
            ],
        },
        {"ingredient_id": ingredients[1]["id"], "skip": True},
    ], lot


@pytest.mark.asyncio
async def test_atomic_deduction_replay_undo(setup):
    client, factory = setup
    state, _ = await start(client, factory)
    state = (await command(client, state, "review", early_finish=True))[0].json()
    uses, lot = await usage(client, state)
    proposal = (
        await client.post(
            f"{PATH}/{state['id']}/consumption-preview", headers=HEADERS, json={"uses": uses}
        )
    ).json()
    assert proposal["lots"][0]["after"] == 0.03
    done, payload = await command(
        client,
        state,
        "complete",
        uses=uses,
        fingerprint=proposal["fingerprint"],
        stock_action="confirm",
    )
    assert done.status_code == 200, done.text
    state = done.json()
    assert (
        await client.post(f"{PATH}/{state['id']}/actions", headers=HEADERS, json=payload)
    ).json()["stock"]["status"] == "applied"
    undone, _ = await command(client, state, "undo")
    assert undone.status_code == 200
    assert (
        await client.post(f"{PATH}/{state['id']}/actions", headers=HEADERS, json=payload)
    ).json()["stock"]["status"] == "undone"
    async with factory() as db:
        assert (await db.get(KitchenItem, uuid.UUID(lot["id"]))).quantity == 0.12


@pytest.mark.asyncio
async def test_unknown_aggregate_and_stale_stock(setup):
    client, factory = setup
    state, _ = await start(client, factory)
    state = (await command(client, state, "review", early_finish=True))[0].json()
    uses, lot = await usage(client, state)
    uses[1] = {"ingredient_id": uses[1]["ingredient_id"], "allocations": uses[0]["allocations"]}
    response = await client.post(
        f"{PATH}/{state['id']}/consumption-preview", headers=HEADERS, json={"uses": uses}
    )
    assert response.status_code == 409 and response.json()["detail"]["code"] == "insufficient_stock"
    uses[1] = {"ingredient_id": uses[1]["ingredient_id"], "skip": True}
    proposal = (
        await client.post(
            f"{PATH}/{state['id']}/consumption-preview", headers=HEADERS, json={"uses": uses}
        )
    ).json()
    async with factory() as db:
        saved = await db.get(KitchenItem, uuid.UUID(lot["id"]))
        saved.version += 1
        await db.commit()
    done, _ = await command(
        client,
        state,
        "complete",
        stock_action="confirm",
        uses=uses,
        fingerprint=proposal["fingerprint"],
    )
    assert done.status_code == 409
    recovered = (await client.get(f"{PATH}/{state['id']}", headers=HEADERS)).json()
    assert recovered["status"] == "awaiting_confirmation" and recovered["stock"] is None


@pytest.mark.asyncio
async def test_unknown_units_foreign_lots_and_undo_conflict(setup):
    client, factory = setup
    state, _ = await start(client, factory)
    state = (await command(client, state, "review", early_finish=True))[0].json()
    uses, lot = await usage(client, state)
    endpoint = f"{PATH}/{state['id']}/consumption-preview"
    allocation = uses[0]["allocations"][0]
    allocation["unit"] = "ml"
    assert (await client.post(endpoint, headers=HEADERS, json={"uses": uses})).status_code == 422
    allocation["unit"] = "g"
    async with factory() as db:
        saved = await db.get(KitchenItem, uuid.UUID(lot["id"]))
        saved.quantity = None
        await db.commit()
    assert (await client.post(endpoint, headers=HEADERS, json={"uses": uses})).status_code == 422
    async with factory() as db:
        saved = await db.get(KitchenItem, uuid.UUID(lot["id"]))
        saved.quantity = 0.12
        bob = await get_user(db, "bob")
        foreign = KitchenItem(
            user_id=bob.id, name="quinoa", quantity=1, unit="kg", location="pantry"
        )
        db.add(foreign)
        await db.commit()
        foreign_id = str(foreign.id)
    allocation["lot_id"] = foreign_id
    assert (await client.post(endpoint, headers=HEADERS, json={"uses": uses})).status_code == 404
    allocation["lot_id"] = lot["id"]
    proposal = (await client.post(endpoint, headers=HEADERS, json={"uses": uses})).json()
    done, _ = await command(
        client,
        state,
        "complete",
        uses=uses,
        fingerprint=proposal["fingerprint"],
        stock_action="confirm",
    )
    assert done.status_code == 200
    async with factory() as db:
        saved = await db.get(KitchenItem, uuid.UUID(lot["id"]))
        saved.quantity = 0.04
        saved.version += 1
        await db.commit()
    assert (await command(client, done.json(), "undo"))[0].status_code == 409
    async with factory() as db:
        assert (await db.get(KitchenItem, uuid.UUID(lot["id"]))).quantity == 0.04


@pytest.mark.asyncio
async def test_timer_pause_resume_restart_and_elapsed_recovery(setup, monkeypatch):
    from fuellayer.modules.cooking import service

    client, factory = setup
    monkeypatch.setattr(service.time, "time", lambda: 1000)
    state, _ = await start(client, factory)
    tid = str(uuid.uuid4())
    state = (await command(client, state, "timer_start", timer_id=tid, duration_seconds=60))[
        0
    ].json()
    monkeypatch.setattr(service.time, "time", lambda: 1020)
    state = (await command(client, state, "timer_pause", timer_id=tid))[0].json()
    assert state["timers"][0]["remaining"] == 40
    monkeypatch.setattr(service.time, "time", lambda: 1100)
    state = (await command(client, state, "timer_resume", timer_id=tid))[0].json()
    assert state["timers"][0]["deadline"] == 1140
    state = (await command(client, state, "timer_restart", timer_id=tid))[0].json()
    assert state["timers"][0]["deadline"] == 1160
    monkeypatch.setattr(service.time, "time", lambda: 1200)
    recovered = (await client.get(f"{PATH}/{state['id']}", headers=HEADERS)).json()
    assert recovered["timers"][0]["deadline"] < recovered["server_now"]
    state = (await command(client, recovered, "timer_pause", timer_id=tid))[0].json()
    assert state["timers"][0]["status"] == "elapsed"


@pytest.mark.asyncio
async def test_catalogue_only_starts_with_authored_steps(setup, monkeypatch):
    from dataclasses import replace

    from fuellayer.modules.recipes import service

    client, _ = setup
    meal = service.MEAL_CATALOG[0]
    body = {"request_id": str(uuid.uuid4()), "recipe_id": meal.id, "portions": 2}
    assert (await client.post(PATH, headers=HEADERS, json=body)).status_code == 422
    monkeypatch.setattr(
        service, "MEAL_CATALOG", (replace(meal, steps=("Use the original instructions.",)),)
    )
    response = await client.post(PATH, headers=HEADERS, json=body)
    assert response.status_code == 201, response.text
    assert (
        response.json()["snapshot"]["recipe"]["steps"][0]["text"]
        == "Use the original instructions."
    )
    assert (
        await client.post(PATH, headers=HEADERS, json={**body, "user_id": str(uuid.uuid4())})
    ).status_code == 422


def test_quantities_reject_boolean_coercion():
    from pydantic import ValidationError

    from fuellayer.modules.cooking.schemas import Start

    with pytest.raises(ValidationError):
        Start(request_id=uuid.uuid4(), recipe_id="anything", portions=True)


def test_timer_ranges_and_unsupported_fraction():
    assert timer_candidates([{"id": "a", "text": "Rest 1/2 hour."}]) == []
    rows = timer_candidates([{"id": "a", "text": "Wait 10 to 15 minutes. Cuire 2 à 3 minutes."}])
    assert [(r["minimum"], r["maximum"]) for r in rows] == [(600, 900), (120, 180)]
