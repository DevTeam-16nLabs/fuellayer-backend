from datetime import UTC, datetime
from typing import Literal, cast

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fuellayer.core.auth import delete_clerk_user
from fuellayer.modules.onboarding.engine import build_preview
from fuellayer.modules.onboarding.models import (
    AccountDeletionRequest,
    BodyMeasurement,
    ClerkWebhookEvent,
    FoodPreference,
    Goal,
    GroceryListRecord,
    NutritionTarget,
    OnboardingCompletion,
    PlanningProfile,
    Profile,
    StarterPlanRecord,
    User,
)
from fuellayer.modules.onboarding.schemas import (
    AccountDeletionResponse,
    ActivityAnswers,
    ActivityBand,
    AllergenCode,
    BootstrapResponse,
    BootstrapUser,
    CookingTimeBand,
    DietaryPattern,
    EquationSex,
    FoodAnswersV2,
    GoalAnswers,
    GoalDetail,
    GoalType,
    OnboardingAnswers,
    OnboardingAnswersV1,
    OnboardingAnswersV2,
    PlanningProfileUpgrade,
    PlanStatus,
    ProfileAnswers,
    ShoppingCadence,
    StarterPlanPreview,
    StarterPlanPreviewV1,
    StarterPlanPreviewV2,
    TrainingFocus,
    UnitSystem,
)


def _already_completed() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "onboarding_already_completed",
            "message": "This account already has a saved onboarding plan.",
        },
    )


async def get_bootstrap(session: AsyncSession, clerk_subject: str) -> BootstrapResponse:
    result = await session.execute(
        select(User)
        .where(User.clerk_subject == clerk_subject)
        .options(selectinload(User.starter_plan), selectinload(User.planning_profile))
    )
    user = result.scalar_one_or_none()
    if user is None:
        return BootstrapResponse(
            user=BootstrapUser(id=None, onboarding_status="not_started"), plan=None
        )
    plan: StarterPlanPreview | None = None
    if user.starter_plan is not None:
        payload = user.starter_plan.preview_payload
        if payload.get("schema_version") == 2:
            plan = StarterPlanPreviewV2.model_validate(payload)
        else:
            plan = StarterPlanPreviewV1.model_validate(payload)
    onboarding_status = cast(
        Literal["not_started", "completed"],
        user.onboarding_status,
    )
    return BootstrapResponse(
        user=BootstrapUser(
            id=str(user.id),
            onboarding_status=onboarding_status,
            planning_profile_version=(
                2
                if user.planning_profile is not None
                else 1
                if onboarding_status == "completed"
                else None
            ),
        ),
        plan=plan,
    )


async def complete_onboarding(
    session: AsyncSession,
    clerk_subject: str,
    idempotency_key: str,
    answers: OnboardingAnswers,
) -> BootstrapResponse:
    existing_completion = await session.scalar(
        select(OnboardingCompletion).where(
            OnboardingCompletion.clerk_subject == clerk_subject,
            OnboardingCompletion.idempotency_key == idempotency_key,
        )
    )
    if existing_completion is not None:
        return await get_bootstrap(session, clerk_subject)

    user = await session.scalar(select(User).where(User.clerk_subject == clerk_subject))
    if user is not None and user.onboarding_status == "completed":
        raise _already_completed()

    preview = build_preview(answers)
    if preview.status == PlanStatus.CONSTRAINED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "plan_constraints_unresolved",
                "message": "The current catalog cannot safely save this combination yet.",
            },
        )

    try:
        if user is None:
            user = User(clerk_subject=clerk_subject)
            session.add(user)
            await session.flush()

        user.onboarding_status = "completed"
        session.add_all(
            [
                Profile(
                    user_id=user.id,
                    age_at_onboarding=answers.profile.age,
                    height_cm=answers.profile.height_cm,
                    equation_sex=answers.profile.equation_sex.value,
                    locale=answers.locale,
                    preferred_units=answers.units.value,
                ),
                BodyMeasurement(user_id=user.id, weight_kg=answers.profile.weight_kg),
                Goal(
                    user_id=user.id,
                    goal_type=answers.goal.type.value,
                    goal_detail=answers.goal.detail.value if answers.goal.detail else None,
                    daily_movement=answers.activity.daily_movement.value,
                    training_days=answers.activity.training_days,
                    training_focus=answers.activity.training_focus.value,
                ),
                FoodPreference(
                    user_id=user.id,
                    dietary_pattern=answers.food.dietary_pattern.value,
                    allergens=[allergen.value for allergen in answers.food.allergens],
                    meals_per_day=answers.food.meals_per_day,
                    include_breakfast=answers.food.include_breakfast,
                    include_snacks=(
                        answers.food.include_snacks
                        if isinstance(answers, OnboardingAnswersV1)
                        else bool(answers.food.snack_slots)
                    ),
                    cooking_time=answers.food.cooking_time.value,
                    servings=(
                        answers.food.servings if isinstance(answers, OnboardingAnswersV1) else 1
                    ),
                    shopping_cadence=(
                        answers.food.shopping_cadence.value
                        if isinstance(answers, OnboardingAnswersV1)
                        else answers.shopping.cadence.value
                    ),
                ),
                NutritionTarget(
                    user_id=user.id,
                    daily_energy_kcal=preview.daily_energy_kcal,
                    protein_g=preview.macros.protein_g,
                    carbohydrates_g=preview.macros.carbohydrates_g,
                    fat_g=preview.macros.fat_g,
                    confidence=preview.confidence.value,
                    engine_version=preview.engine_version,
                    calculation_input_hash=preview.input_hash,
                ),
                StarterPlanRecord(
                    user_id=user.id,
                    preview_payload=preview.model_dump(mode="json"),
                    content_version=preview.content_version,
                ),
                GroceryListRecord(
                    user_id=user.id,
                    grouped_items=[
                        section.model_dump(mode="json")
                        for section in (
                            preview.grocery
                            if isinstance(preview, StarterPlanPreviewV1)
                            else preview.grocery.main_trip
                        )
                    ],
                    content_version=preview.content_version,
                ),
                OnboardingCompletion(
                    user_id=user.id,
                    clerk_subject=clerk_subject,
                    idempotency_key=idempotency_key,
                    schema_version=answers.schema_version,
                    engine_version=preview.engine_version,
                    input_hash=preview.input_hash,
                ),
            ]
        )
        if isinstance(answers, OnboardingAnswersV2):
            session.add(
                PlanningProfile(
                    user_id=user.id,
                    schema_version=2,
                    living_arrangement=answers.household.living_arrangement.value,
                    household_size=answers.household.household_size,
                    meal_contexts=[
                        context.model_dump(mode="json") for context in answers.meal_contexts
                    ],
                    shopping_cadence=answers.shopping.cadence.value,
                    location_status=answers.shopping.location_status.value,
                    location_source=(
                        answers.shopping.location_source.value
                        if answers.shopping.location_source
                        else None
                    ),
                    area_label=answers.shopping.area_label,
                    country_code=answers.shopping.country_code,
                    preferred_place_ids=answers.shopping.preferred_place_ids,
                )
            )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        completion = await session.scalar(
            select(OnboardingCompletion).where(
                OnboardingCompletion.clerk_subject == clerk_subject,
                OnboardingCompletion.idempotency_key == idempotency_key,
            )
        )
        if completion is None:
            raise

    return await get_bootstrap(session, clerk_subject)


async def upgrade_planning_profile(
    session: AsyncSession,
    clerk_subject: str,
    idempotency_key: str,
    upgrade: PlanningProfileUpgrade,
) -> BootstrapResponse:
    del idempotency_key  # The deterministic input hash makes this upsert replay-safe.
    user = await session.scalar(
        select(User)
        .where(User.clerk_subject == clerk_subject)
        .options(
            selectinload(User.profile),
            selectinload(User.measurements),
            selectinload(User.goal),
            selectinload(User.food_preferences),
            selectinload(User.planning_profile),
            selectinload(User.starter_plan),
            selectinload(User.grocery_list),
        )
    )
    if (
        user is None
        or user.onboarding_status != "completed"
        or user.profile is None
        or not user.measurements
        or user.goal is None
        or user.food_preferences is None
        or user.starter_plan is None
        or user.grocery_list is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "onboarding_not_completed",
                "message": "Complete your original onboarding before upgrading the plan.",
            },
        )

    food = user.food_preferences
    goal_detail = GoalDetail(user.goal.goal_detail) if user.goal.goal_detail else None
    snack_slots = [0] if food.include_snacks else []
    latest_measurement = max(user.measurements, key=lambda item: item.measured_at)
    answers = OnboardingAnswersV2(
        locale=upgrade.locale,
        units=UnitSystem(user.profile.preferred_units),
        goal=GoalAnswers(type=GoalType(user.goal.goal_type), detail=goal_detail),
        profile=ProfileAnswers(
            age=user.profile.age_at_onboarding,
            height_cm=user.profile.height_cm,
            weight_kg=latest_measurement.weight_kg,
            equation_sex=EquationSex(user.profile.equation_sex),
        ),
        activity=ActivityAnswers(
            daily_movement=ActivityBand(user.goal.daily_movement),
            training_days=user.goal.training_days,
            training_focus=TrainingFocus(user.goal.training_focus),
        ),
        food=FoodAnswersV2(
            dietary_pattern=DietaryPattern(food.dietary_pattern),
            allergens=[AllergenCode(value) for value in food.allergens],
            meals_per_day=food.meals_per_day,
            include_breakfast=food.include_breakfast,
            snack_slots=snack_slots,
            cooking_time=CookingTimeBand(food.cooking_time),
        ),
        household=upgrade.household,
        meal_contexts=upgrade.meal_contexts,
        shopping=upgrade.shopping.model_copy(
            update={"cadence": ShoppingCadence(food.shopping_cadence)}
        ),
    )
    preview = build_preview(answers)
    assert isinstance(preview, StarterPlanPreviewV2)
    if preview.status == PlanStatus.CONSTRAINED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "plan_constraints_unresolved",
                "message": "The current catalog cannot safely save this combination yet.",
            },
        )

    values = {
        "schema_version": 2,
        "living_arrangement": upgrade.household.living_arrangement.value,
        "household_size": upgrade.household.household_size,
        "meal_contexts": [context.model_dump(mode="json") for context in upgrade.meal_contexts],
        "shopping_cadence": food.shopping_cadence,
        "location_status": upgrade.shopping.location_status.value,
        "location_source": (
            upgrade.shopping.location_source.value if upgrade.shopping.location_source else None
        ),
        "area_label": upgrade.shopping.area_label,
        "country_code": upgrade.shopping.country_code,
        "preferred_place_ids": upgrade.shopping.preferred_place_ids,
    }
    if user.planning_profile is None:
        user.planning_profile = PlanningProfile(**values)
    else:
        for field, value in values.items():
            setattr(user.planning_profile, field, value)
    user.starter_plan.preview_payload = preview.model_dump(mode="json")
    user.starter_plan.content_version = preview.content_version
    user.grocery_list.grouped_items = [
        section.model_dump(mode="json") for section in preview.grocery.main_trip
    ]
    user.grocery_list.content_version = preview.content_version
    await session.commit()
    return await get_bootstrap(session, clerk_subject)


async def delete_account(session: AsyncSession, clerk_subject: str) -> AccountDeletionResponse:
    user = await session.scalar(select(User).where(User.clerk_subject == clerk_subject))
    existing_request = await session.scalar(
        select(AccountDeletionRequest).where(AccountDeletionRequest.clerk_subject == clerk_subject)
    )
    deletion_request = existing_request or AccountDeletionRequest(clerk_subject=clerk_subject)
    if existing_request is None:
        session.add(deletion_request)
    if user is not None:
        await session.delete(user)
    deletion_request.attempts += 1
    await session.commit()

    try:
        await delete_clerk_user(clerk_subject)
    except Exception as exc:  # Clerk availability must not retain local health data.
        deletion_request.status = "pending"
        deletion_request.last_error = str(exc)[:500]
        await session.commit()
        return AccountDeletionResponse(status="clerk_deletion_pending")

    deletion_request.status = "completed"
    deletion_request.last_error = None
    deletion_request.completed_at = datetime.now(UTC)
    await session.commit()
    return AccountDeletionResponse(status="deleted")


async def process_clerk_webhook(
    session: AsyncSession,
    event_id: str,
    event_type: str,
    data: dict[str, object],
) -> bool:
    if await session.scalar(
        select(ClerkWebhookEvent).where(ClerkWebhookEvent.event_id == event_id)
    ):
        return False

    if event_type == "user.deleted":
        subject = data.get("id")
        if isinstance(subject, str):
            await session.execute(delete(User).where(User.clerk_subject == subject))

    session.add(ClerkWebhookEvent(event_id=event_id, event_type=event_type))
    await session.commit()
    return True
