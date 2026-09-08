from copy import deepcopy

import httpx
import pytest
from fastapi import HTTPException, Request
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import Base, get_session
from fuellayer.main import create_app
from fuellayer.modules.onboarding.models import FoodPreference, StarterPlanRecord, User
from fuellayer.modules.recipes.models import RecipeBookmark
from fuellayer.modules.recipes.service import build_library


def preference() -> FoodPreference:
    return FoodPreference(
        dietary_pattern="vegan",
        allergens=["soy"],
        meals_per_day=3,
        include_breakfast=True,
        include_snacks=False,
        cooking_time="30_min",
        servings=1,
        shopping_cadence="weekly",
    )


def test_catalogue_preserves_estimates_missing_steps_and_portion_basis() -> None:
    public = build_library()
    assert len(public.items) == len({item.id for item in public.items}) == 16
    assert all(item.compatibility == "unknown" and not item.saved for item in public.items)
    plan = {
        "days": [
            {
                "meals": [
                    {
                        "name": "Lemon chickpea quinoa",
                        "calories_kcal": 900,
                        "portions": 1.5,
                        "servings": 4,
                    }
                ]
            }
        ]
    }
    before = deepcopy(plan)
    library = build_library(preference(), plan, {"lunch-tofu-rice"})
    quinoa = next(item for item in library.items if item.id == "lunch-quinoa-chickpea")
    assert quinoa.in_plan and quinoa.compatibility == "matches"
    assert quinoa.nutrition.calories_kcal == 590  # base serving, not 900 or 900 × 4
    assert quinoa.nutrition.reference_servings == 1
    assert quinoa.nutrition.status == "estimated" and quinoa.steps_status == "missing"
    tofu = next(item for item in library.items if item.id == "lunch-tofu-rice")
    assert tofu.saved and tofu.compatibility == "conflict"  # saved ≠ suitable
    assert library.inventory_status == "unavailable"
    assert plan == before
    legacy = build_library(preference(), {"meals": [{"name": "Lemon chickpea quinoa"}]})
    assert next(item for item in legacy.items if item.id == quinoa.id).in_plan


@pytest.mark.asyncio
async def test_authenticated_bookmarks_are_idempotent_isolated_and_deleted_with_account() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _) -> None:  # type: ignore[no-untyped-def]
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    plan = {"schema_version": 2, "days": [{"meals": [{"name": "Lemon chickpea quinoa"}]}]}
    async with factory() as session:
        for subject in ["alice", "bob"]:
            session.add(
                User(
                    clerk_subject=subject,
                    food_preferences=preference(),
                    starter_plan=StarterPlanRecord(
                        preview_payload=deepcopy(plan), content_version="test"
                    ),
                )
            )
        await session.commit()
    app = create_app()

    async def authenticated(request: Request) -> AuthSubject:
        subject = request.headers.get("x-test-user")
        if subject not in {"alice", "bob"}:
            raise HTTPException(401)
        return AuthSubject(subject)

    async def sessions():  # type: ignore[no-untyped-def]
        async with factory() as session:
            yield session

    app.dependency_overrides[require_auth_subject] = authenticated
    app.dependency_overrides[get_session] = sessions
    path = "/api/v1/recipes"
    bookmark = path + "/lunch-quinoa-chickpea/saved"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get(path)).status_code == 401
        assert (await client.put(bookmark, json={"saved": True})).status_code == 401
        assert (await client.get(path + "/catalogue")).status_code == 200
        alice, bob = {"x-test-user": "alice"}, {"x-test-user": "bob"}
        for _ in range(2):
            response = await client.put(bookmark, json={"saved": True}, headers=alice)
            assert response.status_code == 200 and response.json()["saved"] is True
        items = (await client.get(path, headers=alice)).json()["items"]
        assert [item["id"] for item in items if item["saved"]] == ["lunch-quinoa-chickpea"]
        assert not any(
            item["saved"] for item in (await client.get(path, headers=bob)).json()["items"]
        )
        assert (
            await client.put(path + "/fake/saved", json={"saved": True}, headers=alice)
        ).status_code == 404
        assert (
            await client.put(bookmark, json={"saved": "false"}, headers=alice)
        ).status_code == 422
        for _ in range(2):
            assert (
                await client.put(bookmark, json={"saved": False}, headers=alice)
            ).status_code == 200
        assert not any(
            item["saved"] for item in (await client.get(path, headers=alice)).json()["items"]
        )
        await client.put(bookmark, json={"saved": True}, headers=alice)
    async with factory() as session:
        assert len(list(await session.scalars(select(RecipeBookmark)))) == 1
        plans = list(await session.scalars(select(StarterPlanRecord)))
        assert all(record.preview_payload == plan for record in plans)
        user = await session.scalar(select(User).where(User.clerk_subject == "alice"))
        await session.delete(user)
        await session.commit()
        assert not list(await session.scalars(select(RecipeBookmark)))
    await engine.dispose()


@pytest.mark.asyncio
async def test_recipe_detail_exposes_only_catalogue_quantities_and_no_invented_steps() -> None:
    from fuellayer.modules.recipes.service import build_detail

    detail = build_detail("lunch-quinoa-chickpea")
    assert detail.ingredients_reference_servings == detail.nutrition.reference_servings == 1
    assert [(i.name, i.quantity, i.unit) for i in detail.ingredients] == [
        ("quinoa", 90, "g"),
        ("chickpeas", 180, "g"),
        ("cucumber", 1, "item"),
        ("lemon", 1, "item"),
        ("parsley", 20, "g"),
    ]
    assert detail.nutrition.calories_kcal == 590
    assert detail.macros["protein_g"].value == 23
    assert all(item.status == "estimated" for item in detail.macros.values())
    assert detail.steps_status == "missing"
    assert detail.compatibility == "unknown" and not detail.saved
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/recipes/catalogue/lunch-quinoa-chickpea")
        assert response.status_code == 200
        assert response.json() == detail.model_dump()
        assert (await client.get("/api/v1/recipes/catalogue/unknown")).status_code == 404
