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
from fuellayer.modules.onboarding.models import GroceryListRecord, PlanningProfile
from fuellayer.modules.onboarding.router import preview_rate_limiter
from fuellayer.modules.onboarding.schemas import (
    ActivityAnswers,
    ActivityBand,
    AllergenCode,
    CookingTimeBand,
    DietaryPattern,
    EquationSex,
    FoodAnswers,
    FoodAnswersV2,
    GoalAnswers,
    GoalDetail,
    GoalType,
    HouseholdProfile,
    LivingArrangement,
    LocationSource,
    LocationStatus,
    MealAudience,
    MealContext,
    MealContextSlot,
    OnboardingAnswersV1,
    OnboardingAnswersV2,
    PlanningProfileUpgrade,
    PlanStatus,
    ProfileAnswers,
    ShoppingCadence,
    ShoppingProfile,
    StarterPlanPreviewV2,
    TrainingFocus,
    UnitSystem,
)
from fuellayer.modules.onboarding.service import (
    complete_onboarding,
    delete_account,
    get_bootstrap,
    process_clerk_webhook,
    upgrade_planning_profile,
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


def answers_v2(
    *,
    cadence: ShoppingCadence = ShoppingCadence.WEEKLY,
    dinner_audience: MealAudience = MealAudience.JUST_ME,
    dinner_shared_days: int = 0,
    dinner_servings: int = 1,
) -> OnboardingAnswersV2:
    household_size = max(2, dinner_servings) if dinner_audience != MealAudience.JUST_ME else 1
    arrangement = (
        LivingArrangement.FAMILY
        if dinner_audience != MealAudience.JUST_ME
        else LivingArrangement.ALONE
    )
    return OnboardingAnswersV2(
        units=UnitSystem.METRIC,
        goal=GoalAnswers(type=GoalType.MAINTAIN),
        profile=ProfileAnswers(
            age=32,
            height_cm=178,
            weight_kg=78,
            equation_sex=EquationSex.UNSPECIFIED,
        ),
        activity=ActivityAnswers(
            daily_movement=ActivityBand.MIXED_MOVEMENT,
            training_days=3,
            training_focus=TrainingFocus.MIXED,
        ),
        food=FoodAnswersV2(
            dietary_pattern=DietaryPattern.NONE,
            allergens=[],
            meals_per_day=3,
            include_breakfast=True,
            snack_slots=[],
            cooking_time=CookingTimeBand.THIRTY,
        ),
        household=HouseholdProfile(
            living_arrangement=arrangement,
            household_size=household_size,
        ),
        meal_contexts=[
            MealContext(
                slot=MealContextSlot.BREAKFAST,
                audience=MealAudience.JUST_ME,
                shared_days_per_week=0,
                shared_servings=1,
            ),
            MealContext(
                slot=MealContextSlot.LUNCH,
                audience=MealAudience.JUST_ME,
                shared_days_per_week=0,
                shared_servings=1,
            ),
            MealContext(
                slot=MealContextSlot.DINNER,
                audience=dinner_audience,
                shared_days_per_week=dinner_shared_days,
                shared_servings=dinner_servings,
            ),
        ],
        shopping=ShoppingProfile(
            cadence=cadence,
            location_status=LocationStatus.SELECTED,
            location_source=LocationSource.MANUAL,
            area_label="Dakar",
            country_code="SN",
            preferred_place_ids=["store-1"],
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


def test_v2_preview_builds_a_deterministic_week() -> None:
    first = build_preview(answers_v2())
    second = build_preview(answers_v2())

    assert isinstance(first, StarterPlanPreviewV2)
    assert first == second
    assert len(first.days) == 7
    assert all(day.meals for day in first.days)
    assert first.grocery.horizon_days == 7


def test_shared_meals_increase_weekly_grocery_quantities() -> None:
    solo = build_preview(answers_v2())
    family = build_preview(
        answers_v2(
            dinner_audience=MealAudience.MIXED,
            dinner_shared_days=4,
            dinner_servings=4,
        )
    )
    assert isinstance(solo, StarterPlanPreviewV2)
    assert isinstance(family, StarterPlanPreviewV2)

    def total_quantity(preview: StarterPlanPreviewV2) -> float:
        return sum(item.quantity for section in preview.grocery.main_trip for item in section.items)

    assert total_quantity(family) > total_quantity(solo)
    assert (
        sum(
            meal.audience == MealAudience.SHARED
            for day in family.days
            for meal in day.meals
            if meal.slot == "Dinner"
        )
        == 4
    )


def test_monthly_v2_plan_splits_three_fresh_refreshes() -> None:
    preview = build_preview(answers_v2(cadence=ShoppingCadence.MONTHLY))

    assert isinstance(preview, StarterPlanPreviewV2)
    assert preview.grocery.horizon_days == 28
    assert [refresh.day_offset for refresh in preview.grocery.fresh_refreshes] == [7, 14, 21]


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


def test_v2_preview_api_contract() -> None:
    preview_rate_limiter.clear()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/onboarding/preview",
            json=answers_v2(cadence=ShoppingCadence.TWICE_MONTHLY).model_dump(mode="json"),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 2
    assert len(body["days"]) == 7
    assert body["grocery"]["horizon_days"] == 14


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
async def test_v2_completion_persists_only_coarse_location_and_place_ids() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        result = await complete_onboarding(session, "user_v2", "onboarding-v2-key", answers_v2())
        profile = await session.scalar(select(PlanningProfile))

    assert result.user.planning_profile_version == 2
    assert result.plan is not None and result.plan.schema_version == 2
    assert profile is not None
    assert profile.area_label == "Dakar"
    assert profile.country_code == "SN"
    assert profile.preferred_place_ids == ["store-1"]
    assert not hasattr(profile, "latitude")
    assert not hasattr(profile, "longitude")
    await engine.dispose()


@pytest.mark.asyncio
async def test_v1_profile_upgrade_is_replay_safe_and_regenerates_v2_plan() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    upgrade = PlanningProfileUpgrade(
        household=HouseholdProfile(
            living_arrangement=LivingArrangement.PARTNER,
            household_size=2,
        ),
        meal_contexts=[
            MealContext(
                slot=slot,
                audience=(
                    MealAudience.SHARED if slot == MealContextSlot.DINNER else MealAudience.JUST_ME
                ),
                shared_days_per_week=7 if slot == MealContextSlot.DINNER else 0,
                shared_servings=2 if slot == MealContextSlot.DINNER else 1,
            )
            for slot in (
                MealContextSlot.BREAKFAST,
                MealContextSlot.LUNCH,
                MealContextSlot.DINNER,
            )
        ],
        shopping=ShoppingProfile(
            cadence=ShoppingCadence.MONTHLY,
            location_status=LocationStatus.SKIPPED,
        ),
    )

    async with session_factory() as session:
        original = await complete_onboarding(session, "user_upgrade", "onboarding-key", answers())
        first = await upgrade_planning_profile(session, "user_upgrade", "upgrade-key", upgrade)
        replay = await upgrade_planning_profile(session, "user_upgrade", "upgrade-key", upgrade)

    assert original.user.planning_profile_version == 1
    assert first == replay
    assert first.user.planning_profile_version == 2
    assert first.plan is not None and first.plan.schema_version == 2
    # Upgrade preserves the account's established grocery cadence.
    assert first.plan.grocery.horizon_days == 7
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
