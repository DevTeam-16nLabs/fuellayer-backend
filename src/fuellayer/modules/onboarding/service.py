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
    Profile,
    StarterPlanRecord,
    User,
)
from fuellayer.modules.onboarding.schemas import (
    AccountDeletionResponse,
    BootstrapResponse,
    BootstrapUser,
    OnboardingAnswersV1,
    PlanStatus,
    StarterPlanPreviewV1,
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
        .options(selectinload(User.starter_plan))
    )
    user = result.scalar_one_or_none()
    if user is None:
        return BootstrapResponse(
            user=BootstrapUser(id=None, onboarding_status="not_started"), plan=None
        )
    plan = None
    if user.starter_plan is not None:
        plan = StarterPlanPreviewV1.model_validate(user.starter_plan.preview_payload)
    onboarding_status = cast(
        Literal["not_started", "completed"],
        user.onboarding_status,
    )
    return BootstrapResponse(
        user=BootstrapUser(id=str(user.id), onboarding_status=onboarding_status),
        plan=plan,
    )


async def complete_onboarding(
    session: AsyncSession,
    clerk_subject: str,
    idempotency_key: str,
    answers: OnboardingAnswersV1,
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
                    include_snacks=answers.food.include_snacks,
                    cooking_time=answers.food.cooking_time.value,
                    servings=answers.food.servings,
                    shopping_cadence=answers.food.shopping_cadence.value,
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
                    grouped_items=[section.model_dump(mode="json") for section in preview.grocery],
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
