from collections.abc import AsyncIterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fuellayer.core.database import Base
from fuellayer.main import app
from fuellayer.modules.onboarding import engine as engine_module
from fuellayer.modules.onboarding.engine import build_preview
from fuellayer.modules.onboarding.models import GroceryListRecord
from fuellayer.modules.onboarding.router import preview_rate_limiter
from fuellayer.modules.onboarding.schemas import (
    ActivityAnswers,
    ActivityBand,
    AllergenCode,
    CookingTimeBand,
    DietaryPattern,
    EquationSex,
    FoodAnswers,
    GoalAnswers,
    GoalDetail,
    GoalType,
    OnboardingAnswersV1,
    PlanStatus,
    ProfileAnswers,
    ShoppingCadence,
    TrainingFocus,
    UnitSystem,
)
from fuellayer.modules.onboarding.service import (
    complete_onboarding,
    delete_account,
    get_bootstrap,
    process_clerk_webhook,
)


def answers(
    *,
    goal: GoalType = GoalType.MAINTAIN,
    detail: GoalDetail | None = None,
    equation_sex: EquationSex = EquationSex.UNSPECIFIED,
    age: int = 32,
    pattern: DietaryPattern = DietaryPattern.NONE,
    allergens: list[AllergenCode] | None = None,
) -> OnboardingAnswersV1:
    return OnboardingAnswersV1(
        units=UnitSystem.METRIC,
        goal=GoalAnswers(type=goal, detail=detail),
        profile=ProfileAnswers(
            age=age,
            height_cm=178,
            weight_kg=78,
            equation_sex=equation_sex,
        ),
        activity=ActivityAnswers(
            daily_movement=ActivityBand.MIXED_MOVEMENT,
            training_days=3,
            training_focus=TrainingFocus.MIXED,
        ),
        food=FoodAnswers(
            dietary_pattern=pattern,
            allergens=allergens or [],
            meals_per_day=3,
            include_breakfast=True,
            include_snacks=False,
            cooking_time=CookingTimeBand.THIRTY,
            servings=1,
            shopping_cadence=ShoppingCadence.WEEKLY,
        ),
    )


def test_preview_is_deterministic_and_complete() -> None:
    first = build_preview(answers())
    second = build_preview(answers())

    assert first == second
    assert first.status == PlanStatus.READY
    assert len(first.meals) == 3
    assert first.grocery
    assert first.confidence.value == "wider"


def test_goal_direction_changes_energy_target() -> None:
    loss = build_preview(answers(goal=GoalType.LOSE_FAT, detail=GoalDetail.GENTLE))
    maintain = build_preview(answers())
    gain = build_preview(answers(goal=GoalType.BUILD_MUSCLE, detail=GoalDetail.SLOW))

    assert loss.daily_energy_kcal < maintain.daily_energy_kcal < gain.daily_energy_kcal


def test_performance_goal_builds_a_ready_plan() -> None:
    preview = build_preview(answers(goal=GoalType.FUEL_PERFORMANCE))

    assert preview.status == PlanStatus.READY
    assert "fuel performance" in preview.explanations[0]


def test_allergens_are_hard_exclusions() -> None:
    preview = build_preview(
        answers(
            pattern=DietaryPattern.VEGETARIAN,
            allergens=[AllergenCode.MILK, AllergenCode.EGGS, AllergenCode.GLUTEN],
        )
    )
    forbidden = {"Greek yogurt", "eggs", "rolled oats", "pasta"}
    ingredients = {item.name for meal in preview.meals for item in meal.ingredients}

    assert preview.status == PlanStatus.READY
    assert ingredients.isdisjoint(forbidden)


def test_empty_catalog_returns_constrained_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine_module, "MEAL_CATALOG", ())

    preview = build_preview(answers())

    assert preview.status == PlanStatus.CONSTRAINED
    assert preview.meals == []
    assert preview.grocery == []
    assert any("allergies" in warning for warning in preview.warnings)


def test_underage_response_has_stable_error_code() -> None:
    preview_rate_limiter.clear()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/onboarding/preview",
            json=answers(age=17).model_dump(mode="json"),
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "underage_not_supported"


def test_preview_api_contract() -> None:
    preview_rate_limiter.clear()
    with TestClient(app) as client:
        response = client.post("/api/v1/onboarding/preview", json=answers().model_dump(mode="json"))

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    assert body["engine_version"]
    assert body["input_hash"]
    assert body["meals"]


@pytest.mark.asyncio
async def test_completion_is_atomic_and_idempotent() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_scope() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async for session in session_scope():
        first = await complete_onboarding(session, "user_test", "onboarding-key-1", answers())
        second = await complete_onboarding(session, "user_test", "onboarding-key-1", answers())
        bootstrap = await get_bootstrap(session, "user_test")
        grocery_count = await session.scalar(select(func.count(GroceryListRecord.id)))

    assert first == second == bootstrap
    assert first.user.onboarding_status == "completed"
    assert first.plan is not None
    assert grocery_count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_completion_rejects_a_second_plan_with_a_new_key() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await complete_onboarding(session, "user_duplicate", "onboarding-key-1", answers())
        with pytest.raises(HTTPException) as caught:
            await complete_onboarding(session, "user_duplicate", "onboarding-key-2", answers())

    assert caught.value.status_code == 409
    await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_replay_is_idempotent() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        first = await process_clerk_webhook(
            session, "event_1", "user.updated", {"id": "user_webhook"}
        )
        replay = await process_clerk_webhook(
            session, "event_1", "user.updated", {"id": "user_webhook"}
        )

    assert first is True
    assert replay is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_account_deletion_removes_local_product_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def delete_clerk_user_stub(subject: str) -> None:
        assert subject == "user_delete"

    monkeypatch.setattr(
        "fuellayer.modules.onboarding.service.delete_clerk_user",
        delete_clerk_user_stub,
    )
    async with session_factory() as session:
        await complete_onboarding(session, "user_delete", "onboarding-key-1", answers())
        result = await delete_account(session, "user_delete")
        bootstrap = await get_bootstrap(session, "user_delete")

    assert result.status == "deleted"
    assert bootstrap.user.onboarding_status == "not_started"
    assert bootstrap.plan is None
    await engine.dispose()
