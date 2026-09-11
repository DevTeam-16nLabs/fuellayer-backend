"""Exercise cooking against migrated local PostgreSQL; every synthetic write rolls back."""

import asyncio
import runpy
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import engine, get_session
from fuellayer.main import create_app
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.onboarding.models import User
from fuellayer.modules.recipe_imports.schemas import RecipeData
from fuellayer.modules.recipe_imports.service import create_recipe


async def main():
    fixture = runpy.run_path("tests/test_plan_replacements.py")
    subject = "cooking-smoke-" + str(uuid.uuid4())
    app = create_app()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )

        async def session_dep():
            async with factory() as session:
                yield session

        async def auth():
            return AuthSubject(subject=subject)

        app.dependency_overrides[get_session] = session_dep
        app.dependency_overrides[require_auth_subject] = auth
        try:
            async with factory() as session:
                owner = User(clerk_subject=subject, food_preferences=fixture["preference"]())
                session.add(owner)
                await session.flush()
                recipe = await create_recipe(
                    session,
                    owner.id,
                    RecipeData.model_validate(
                        {
                            "fields": {"title": "Cooking transaction fixture", "servings": 2},
                            "ingredients": [
                                {
                                    "name": "quinoa",
                                    "quantity": 90,
                                    "unit": "g",
                                    "amount_kind": "numeric",
                                }
                            ],
                            "steps": [
                                {"text": "Rinse quinoa."},
                                {"text": "Follow the original recipe instructions."},
                            ],
                        }
                    ),
                    "manual",
                )
                lot = KitchenItem(
                    user_id=owner.id, name="quinoa", quantity=0.12, unit="kg", location="pantry"
                )
                session.add(lot)
                await session.commit()
                rid, version, lid = f"import:{recipe.id}", recipe.version, lot.id
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/cooking/"
            ) as client:
                start = {
                    "request_id": str(uuid.uuid4()),
                    "recipe_id": rid,
                    "recipe_version": version,
                    "portions": 2,
                    "acknowledge_draft": True,
                }
                response = await client.post("sessions", json=start)
                assert response.status_code == 201, response.text
                state = response.json()
                assert (await client.post("sessions", json=start)).json()["id"] == state["id"]
                path = f"sessions/{state['id']}/actions"
                review = await client.post(
                    path,
                    json={
                        "request_id": str(uuid.uuid4()),
                        "expected_version": state["version"],
                        "action": "review",
                        "early_finish": True,
                    },
                )
                assert review.status_code == 200, review.text
                state = review.json()
                uses = [
                    {
                        "ingredient_id": state["snapshot"]["ingredients"][0]["id"],
                        "allocations": [
                            {"lot_id": str(lid), "expected_version": 1, "quantity": 90, "unit": "g"}
                        ],
                    }
                ]
                proposal = await client.post(
                    f"sessions/{state['id']}/consumption-preview", json={"uses": uses}
                )
                assert proposal.status_code == 200, proposal.text
                command = {
                    "request_id": str(uuid.uuid4()),
                    "expected_version": state["version"],
                    "action": "complete",
                    "stock_action": "confirm",
                    "uses": uses,
                    "fingerprint": proposal.json()["fingerprint"],
                }
                response = await client.post(path, json=command)
                assert response.status_code == 200, response.text
                state = response.json()
                assert state["stock"]["lots"][0]["after"] == 0.03
                assert (await client.post(path, json=command)).json()["version"] == state["version"]
                response = await client.post(
                    path,
                    json={
                        "request_id": str(uuid.uuid4()),
                        "expected_version": state["version"],
                        "action": "undo",
                    },
                )
                assert response.status_code == 200, response.text
                assert (await client.post(path, json=command)).json()["stock"]["status"] == "undone"
            async with factory() as session:
                assert (await session.get(KitchenItem, lid)).quantity == 0.12
            print(
                "PostgreSQL: start/replay, review, g→kg deduction, "
                "completion replay, undo and replay-after-undo passed."
            )
        finally:
            await transaction.rollback()
    async with engine.connect() as connection:
        assert await connection.scalar(select(User.id).where(User.clerk_subject == subject)) is None
    print("All synthetic cooking data rolled back.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
