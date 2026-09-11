"""Real concurrent connections in a temporary PostgreSQL schema; no account data touched."""

import asyncio
import runpy
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.config import settings
from fuellayer.core.database import Base, engine, get_session
from fuellayer.main import create_app
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.onboarding.models import User
from fuellayer.modules.recipe_imports.schemas import RecipeData
from fuellayer.modules.recipe_imports.service import create_recipe


async def main():
    name = "cooking_test_" + uuid.uuid4().hex
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{name}"'))
    isolated = create_async_engine(
        settings.database_url, connect_args={"server_settings": {"search_path": name}}
    )
    try:
        async with isolated.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(isolated, expire_on_commit=False)
        fixture = runpy.run_path("tests/test_plan_replacements.py")
        async with factory() as db:
            user = User(
                clerk_subject="cooking-concurrency", food_preferences=fixture["preference"]()
            )
            db.add(user)
            await db.flush()
            recipe = await create_recipe(
                db,
                user.id,
                RecipeData.model_validate(
                    {
                        "fields": {"title": "Concurrent fixture", "servings": 1},
                        "ingredients": [
                            {"name": "Rice", "quantity": 50, "unit": "g", "amount_kind": "numeric"}
                        ],
                        "steps": [{"text": "Use original instructions."}],
                    }
                ),
                "manual",
            )
            lot = KitchenItem(
                user_id=user.id, name="Rice", quantity=100, unit="g", location="pantry"
            )
            db.add(lot)
            await db.commit()
            rid, rv, lid = f"import:{recipe.id}", recipe.version, lot.id
        app = create_app()

        async def session_dep():
            async with factory() as db:
                yield db

        async def auth():
            return AuthSubject(subject="cooking-concurrency")

        app.dependency_overrides[get_session] = session_dep
        app.dependency_overrides[require_auth_subject] = auth
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/cooking/"
        ) as client:
            payload = {
                "request_id": str(uuid.uuid4()),
                "recipe_id": rid,
                "recipe_version": rv,
                "portions": 1,
                "acknowledge_draft": True,
            }
            starts = await asyncio.gather(
                client.post("sessions", json=payload),
                client.post("sessions", json={**payload, "request_id": str(uuid.uuid4())}),
            )
            assert sorted(r.status_code for r in starts) == [201, 409], [r.text for r in starts]
            state = next(r.json() for r in starts if r.status_code == 201)
            path = f"sessions/{state['id']}/actions"
            command = {
                "request_id": str(uuid.uuid4()),
                "expected_version": state["version"],
                "action": "progress",
                "current_step": state["current_step"],
            }
            responses = await asyncio.gather(
                client.post(path, json=command),
                client.post(path, json={**command, "request_id": str(uuid.uuid4())}),
            )
            assert sorted(r.status_code for r in responses) == [200, 409]
            state = next(r.json() for r in responses if r.status_code == 200)
            response = await client.post(
                path,
                json={
                    "request_id": str(uuid.uuid4()),
                    "expected_version": state["version"],
                    "action": "review",
                    "early_finish": True,
                },
            )
            assert response.status_code == 200, response.text
            state = response.json()
            uses = [
                {
                    "ingredient_id": state["snapshot"]["ingredients"][0]["id"],
                    "allocations": [
                        {"lot_id": str(lid), "expected_version": 1, "quantity": 50, "unit": "g"}
                    ],
                }
            ]
            proposal = (
                await client.post(
                    f"sessions/{state['id']}/consumption-preview", json={"uses": uses}
                )
            ).json()
            command = {
                "request_id": str(uuid.uuid4()),
                "expected_version": state["version"],
                "action": "complete",
                "stock_action": "confirm",
                "uses": uses,
                "fingerprint": proposal["fingerprint"],
            }
            responses = await asyncio.gather(
                client.post(path, json=command),
                client.post(path, json=command),
                client.post(path, json={**command, "request_id": str(uuid.uuid4())}),
            )
            assert sorted(r.status_code for r in responses) in ([200, 200, 409], [200, 409, 409]), [
                r.text for r in responses
            ]
            async with factory() as db:
                saved = await db.get(KitchenItem, lid)
                assert saved.quantity == 50 and saved.version == 2
            state = (await client.get(f"sessions/{state['id']}")).json()
            undo = {
                "request_id": str(uuid.uuid4()),
                "expected_version": state["version"],
                "action": "undo",
            }
            edit = {
                "request_id": str(uuid.uuid4()),
                "expected_version": 2,
                "name": "Rice",
                "quantity": 60,
                "unit": "g",
                "location": "pantry",
                "storage_date": None,
            }
            raced = await asyncio.gather(
                client.post(path, json=undo),
                client.patch(f"http://test/api/v1/kitchen/items/{lid}", json=edit),
            )
            assert sorted(r.status_code for r in raced) == [200, 409], [r.text for r in raced]
            async with factory() as db:
                saved = await db.get(KitchenItem, lid)
                assert saved.version == 3
                assert saved.quantity == (100 if raced[0].status_code == 200 else 60)
        print(
            "PostgreSQL concurrency: one unfinished start, versioned progress, "
            "concurrent completions, single deduction and undo versus Kitchen edit passed."
        )
    finally:
        await isolated.dispose()
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
        await engine.dispose()
    print("Temporary test schema removed.")


if __name__ == "__main__":
    asyncio.run(main())
