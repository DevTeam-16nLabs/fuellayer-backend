"""Real public sources + authenticated API + PostgreSQL; synthetic writes roll back."""

import asyncio
import copy
import json
import runpy
import uuid

import httpx
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import engine, get_session
from fuellayer.main import create_app
from fuellayer.modules.onboarding.models import GroceryListRecord, StarterPlanRecord, User
from fuellayer.modules.recipe_imports import worker
from fuellayer.modules.recipe_imports.schemas import RecipeData

SOURCES = [
    "https://www.bbcgoodfood.com/recipes/chickpea-salad",
    "https://cookieandkate.com/best-lentil-soup-recipe/",
    "https://www.budgetbytes.com/scallion-herb-chickpea-salad/",
]


async def main():
    fixture = runpy.run_path("tests/test_plan_replacements.py")
    owner = "recipe-smoke-" + str(uuid.uuid4())
    other = owner + "-other"
    app = create_app()
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )

        async def session_dep():
            async with factory() as session:
                yield session

        async def auth(request: Request):
            return AuthSubject(subject=request.headers.get("x-smoke-user", owner))

        app.dependency_overrides[get_session] = session_dep
        app.dependency_overrides[require_auth_subject] = auth
        original = fixture["plan_fixture"]()
        try:
            async with factory() as session:
                for subject in [owner, other]:
                    session.add(
                        User(
                            clerk_subject=subject,
                            onboarding_status="completed",
                            food_preferences=fixture["preference"](),
                            starter_plan=StarterPlanRecord(
                                preview_payload=copy.deepcopy(original), content_version="v1"
                            ),
                            grocery_list=GroceryListRecord(
                                grouped_items=copy.deepcopy(original["grocery"]["main_trip"]),
                                content_version="v1",
                            ),
                        )
                    )
                await session.commit()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1/"
            ) as client:
                for url in SOURCES:
                    headers = {"Idempotency-Key": str(uuid.uuid4())}
                    payload = {"input": {"kind": "url", "url": url}}
                    response = await client.post("recipe-imports", json=payload, headers=headers)
                    assert response.status_code == 202, response.text
                    job = response.json()
                    await worker.process(uuid.UUID(job["job_id"]), factory)
                    status = (await client.get("recipe-imports/" + job["job_id"])).json()
                    if status["state"] != "ready_for_review":
                        print(
                            json.dumps(
                                {"source": url, "state": status["state"], "error": status["error"]}
                            )
                        )
                        continue
                    recipe_id = status["recipe_id"]
                    data = (await client.get("recipes/" + recipe_id)).json()
                    edit = {k: data[k] for k in RecipeData.model_fields} | {
                        "expected_version": data["version"]
                    }
                    edit["fields"]["title"] += " · validation"
                    edited = await client.put(
                        "recipes/imported/" + recipe_id,
                        json=edit,
                        headers={"Idempotency-Key": str(uuid.uuid4())},
                    )
                    assert edited.status_code == 200, edited.text
                    data = edited.json()
                    saved = await client.post(
                        "recipes/imported/" + recipe_id + "/save",
                        json={"expected_version": data["version"], "status": "draft"},
                        headers={"Idempotency-Key": str(uuid.uuid4())},
                    )
                    assert saved.status_code == 200, saved.text
                    library = (await client.get("recipes")).json()
                    assert any(r["id"] == recipe_id and r["saved"] for r in library["items"])
                    replay = await client.post("recipe-imports", json=payload, headers=headers)
                    assert replay.json()["job_id"] == job["job_id"]
                    dedup = await client.post(
                        "recipe-imports",
                        json=payload,
                        headers={"Idempotency-Key": str(uuid.uuid4())},
                    )
                    assert dedup.json()["job_id"] == job["job_id"]
                    assert (
                        await client.get("recipes/" + recipe_id, headers={"x-smoke-user": other})
                    ).status_code == 404
                    async with factory() as session:
                        account = await session.scalar(
                            select(User)
                            .where(User.clerk_subject == owner)
                            .options(
                                selectinload(User.starter_plan), selectinload(User.grocery_list)
                            )
                        )
                        assert account.starter_plan.preview_payload == original
                        assert (
                            account.grocery_list.grouped_items == original["grocery"]["main_trip"]
                        )
                    print(
                        json.dumps(
                            {
                                "source": url,
                                "flow": [
                                    "import",
                                    "extract",
                                    "correct",
                                    "save draft",
                                    "library",
                                    "retry",
                                    "isolation",
                                ],
                                "ingredients": len(data["ingredients"]),
                                "steps": len(data["steps"]),
                                "servings": data["fields"]["servings"],
                                "missing": [i["id"] for i in data["issues"]],
                                "result": "passed",
                            }
                        )
                    )
                # Exercise the configured AI integration with literal recipe text.
                response = await client.post(
                    "recipe-imports",
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                    json={
                        "input": {
                            "kind": "text",
                            "text": (
                                "Lemon chickpeas\nServes 2\n400 g cooked chickpeas\n1 lemon\n"
                                "Olive oil to taste\nDrain the chickpeas.\n"
                                "Stir in lemon juice and olive oil."
                            ),
                        }
                    },
                )
                job = response.json()
                await worker.process(uuid.UUID(job["job_id"]), factory)
                status = (await client.get("recipe-imports/" + job["job_id"])).json()
                print(json.dumps({"text_ai": status["state"], "error": status["error"]}))
        finally:
            await transaction.rollback()
        async with AsyncSession(bind=connection) as session:
            assert await session.scalar(select(User).where(User.clerk_subject == owner)) is None
        print("All synthetic accounts and writes rolled back.")
    await engine.dispose()


asyncio.run(main())
