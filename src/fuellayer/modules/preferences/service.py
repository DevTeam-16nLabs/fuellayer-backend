"""Versioned partial updates; no plan, grocery, stock or cooking writes."""

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, NoReturn, cast

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fuellayer.modules.onboarding.catalog import MEAL_CATALOG
from fuellayer.modules.onboarding.engine import (
    NutritionInputs,
    _energy_target,
    _macro_targets,
    _mifflin_rmr,
)
from fuellayer.modules.onboarding.models import (
    BodyMeasurement,
    NutritionTarget,
    NutritionTargetHistory,
    PlanningProfile,
    PreferenceReceipt,
    User,
)
from fuellayer.modules.onboarding.schemas import (
    ActivityAnswers,
    FoodAnswersV2,
    GoalAnswers,
    HouseholdProfile,
    MealContext,
    OnboardingAnswersV2,
    ProfileAnswers,
    ShoppingProfile,
    UnitSystem,
)
from fuellayer.modules.preferences.constraints import (
    allergy_matches,
    excluded_matches,
    ingredient_key,
)
from fuellayer.modules.preferences.schemas import PreviewRequest, SaveRequest

INGREDIENTS = sorted({ingredient_key(i.name) for m in MEAL_CATALOG for i in m.ingredients})
FIELDS = {
    "units": None,
    "goal": {"type", "detail"},
    "profile": {"age", "height_cm", "weight_kg", "equation_sex"},
    "activity": {"daily_movement", "training_days", "training_focus"},
    "food": {
        "dietary_pattern",
        "allergens",
        "excluded_ingredients",
        "meals_per_day",
        "include_breakfast",
        "snack_slots",
        "cooking_time",
    },
    "household": {"living_arrangement", "household_size"},
    "meal_contexts": None,
    "shopping": {
        "cadence",
        "location_status",
        "location_source",
        "area_label",
        "country_code",
        "preferred_place_ids",
    },
}


def fail(
    message: str, code: str = "preferences_invalid", status: int = 422, **extra: Any
) -> NoReturn:
    raise HTTPException(status, {"code": code, "message": message, **extra})


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def owned(db: AsyncSession, subject: str, lock: bool = False) -> User:
    query = (
        select(User)
        .where(User.clerk_subject == subject)
        .options(
            *[
                selectinload(getattr(User, name))
                for name in (
                    "profile",
                    "measurements",
                    "goal",
                    "food_preferences",
                    "planning_profile",
                    "starter_plan",
                    "nutrition_target",
                )
            ]
        )
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update()
    user = await db.scalar(query)
    if user is None or user.onboarding_status != "completed":
        fail("Complete your profile before editing preferences.", "profile_missing", 404)
    assert user is not None
    if not user.profile or not user.goal or not user.food_preferences or not user.measurements:
        fail("Your saved profile is incomplete. Please try again later.", "profile_incomplete", 409)
    return user


def values(user: User) -> dict[str, Any]:
    p, g, f, h = user.profile, user.goal, user.food_preferences, user.planning_profile
    assert p and g and f
    latest = max(user.measurements, key=lambda m: m.measured_at.replace(tzinfo=UTC).timestamp())
    return {
        "units": p.preferred_units,
        "goal": {"type": g.goal_type, "detail": g.goal_detail},
        "profile": {
            "age": p.current_age or p.age_at_onboarding,
            "height_cm": p.height_cm,
            "weight_kg": latest.weight_kg,
            "equation_sex": p.equation_sex,
        },
        "activity": {
            "daily_movement": g.daily_movement,
            "training_days": g.training_days,
            "training_focus": g.training_focus,
        },
        "food": {
            "dietary_pattern": f.dietary_pattern,
            "allergens": f.allergens,
            "excluded_ingredients": f.excluded_ingredients or [],
            "meals_per_day": f.meals_per_day,
            "include_breakfast": f.include_breakfast,
            "snack_slots": f.snack_slots
            if f.snack_slots is not None
            else ([] if not f.include_snacks else None),
            "cooking_time": f.cooking_time,
        },
        "household": {
            "living_arrangement": h.living_arrangement,
            "household_size": h.household_size,
        }
        if h
        else None,
        "meal_contexts": h.meal_contexts if h else [],
        "shopping": p.legacy_shopping
        if not h and p.legacy_shopping
        else {
            "cadence": h.shopping_cadence if h else f.shopping_cadence,
            "location_status": h.location_status if h else "skipped",
            "location_source": h.location_source if h else None,
            "area_label": h.area_label if h else None,
            "country_code": h.country_code if h else None,
            "preferred_place_ids": h.preferred_place_ids if h else [],
        },
    }


def target(user: User) -> dict[str, Any] | None:
    t = user.nutrition_target
    if not t:
        return None
    return {
        "daily_energy_kcal": t.daily_energy_kcal,
        "macros": {
            "protein_g": t.protein_g,
            "carbohydrates_g": t.carbohydrates_g,
            "fat_g": t.fat_g,
        },
        "confidence": t.confidence,
        "engine_version": t.engine_version,
    }


def merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for field, patch in changes.items():
        if field not in FIELDS:
            fail(f"Unknown preference: {field}.", fields=[field])
        allowed = FIELDS[field]
        if allowed is not None:
            if not isinstance(patch, dict) or set(patch) - allowed:
                fail(f"Invalid fields in {field}.", fields=[field])
            result[field] = {**(result.get(field) or {}), **patch}
        else:
            result[field] = patch
    return result


def validate(v: dict[str, Any]) -> None:
    try:
        integer_fields = [
            ("profile.age", v["profile"]["age"]),
            ("activity.training_days", v["activity"]["training_days"]),
            ("food.meals_per_day", v["food"]["meals_per_day"]),
        ]
        if v["household"] is not None:
            integer_fields.append(
                ("household.household_size", v["household"].get("household_size"))
            )
        for c in v["meal_contexts"]:
            if isinstance(c, dict):
                integer_fields.extend(
                    (f"meal_contexts.{k}", c.get(k))
                    for k in ("shared_days_per_week", "shared_servings")
                )
        if isinstance(v["food"]["snack_slots"], list):
            integer_fields.extend(("food.snack_slots", n) for n in v["food"]["snack_slots"])
        for path, value in integer_fields:
            if type(value) is not int:
                fail("Enter a whole number.", fields=[path])
        UnitSystem(v["units"])
        GoalAnswers.model_validate(v["goal"])
        ProfileAnswers.model_validate(v["profile"])
        if v["profile"]["age"] < 18:
            fail("FuelLayer currently supports adults aged 18 and over.", fields=["profile.age"])
        ActivityAnswers.model_validate(v["activity"])
        # Unknown legacy snack timing is retained, never silently written as [].
        food = {k: val for k, val in v["food"].items() if k != "excluded_ingredients"}
        food["snack_slots"] = food["snack_slots"] if food["snack_slots"] is not None else []
        FoodAnswersV2.model_validate(food)
        exclusions = v["food"]["excluded_ingredients"]
        if (
            not isinstance(exclusions, list)
            or len(exclusions) > 50
            or any(x not in INGREDIENTS for x in exclusions)
            or len(set(exclusions)) != len(exclusions)
        ):
            fail(
                "Choose unique ingredients from the ingredient list (up to 50).",
                fields=["food.excluded_ingredients"],
            )
        ShoppingProfile.model_validate(v["shopping"])
        contexts = v["meal_contexts"]
        if not isinstance(contexts, list) or any(
            not isinstance(c, dict)
            or set(c) != {"slot", "audience", "shared_days_per_week", "shared_servings"}
            for c in contexts
        ):
            fail("Invalid shared-meal fields.", fields=["meal_contexts"])
        if v["household"] is not None:
            HouseholdProfile.model_validate(v["household"])
            for c in contexts:
                MealContext.model_validate(c)
            OnboardingAnswersV2.model_validate(
                {**v, "schema_version": 2, "locale": "en", "food": food}
            )
        elif contexts:
            fail("Add household details before shared meals.", fields=["household"])
        # No coercion of boolean/numeric JSON fields at the boundary.
        for path, val in [
            ("profile." + k, n) for k, n in v["profile"].items() if k != "equation_sex"
        ]:
            if type(val) not in (int, float):
                fail("Enter a number.", fields=[path])
        if type(v["food"]["include_breakfast"]) is not bool:
            fail("Breakfast must be true or false.", fields=["food.include_breakfast"])
    except (ValidationError, ValueError, TypeError) as exc:
        errors = (
            exc.errors(include_url=False, include_context=False, include_input=False)
            if isinstance(exc, ValidationError)
            else []
        )
        fail(
            "Check your preferences. " + (errors[0]["msg"] if errors else "Invalid value."),
            errors=errors,
        )


def calculate(v: dict[str, Any]) -> dict[str, Any]:
    a = NutritionInputs(
        profile=ProfileAnswers.model_validate(v["profile"]),
        goal=GoalAnswers.model_validate(v["goal"]),
        activity=ActivityAnswers.model_validate(v["activity"]),
    )
    energy, confidence, warnings = _energy_target(a)
    _, confidence = _mifflin_rmr(a)
    return {
        "daily_energy_kcal": energy,
        "macros": _macro_targets(a, energy).model_dump(),
        "confidence": confidence.value,
        "warnings": warnings,
    }


def restrictions(user: User, v: dict[str, Any]) -> dict[str, Any]:
    from fuellayer.modules.recipes import resolver

    plan = user.starter_plan
    if not plan:
        return {"plan_revision": None, "items": [], "status": "checked"}
    rows = []
    days = plan.preview_payload.get(
        "days", [{"day": 1, "meals": plan.preview_payload.get("meals", [])}]
    )
    food = v["food"]
    for day in days:
        for meal in day["meals"]:
            reasons = []
            try:
                recipe = resolver.for_meal(user, meal)
            except (HTTPException, KeyError, TypeError, ValueError):
                recipe = None
            if recipe is None:
                if (
                    food["allergens"]
                    or food["excluded_ingredients"]
                    or food["dietary_pattern"] != "none"
                ):
                    reasons.append("Ingredients or restriction information need checking.")
            else:
                allergies = allergy_matches(food["allergens"], recipe.allergens)
                if allergies:
                    reasons.append("Contains: " + ", ".join(allergies).replace("_", " "))
                if food["dietary_pattern"] not in recipe.patterns:
                    reasons.append("Does not match your dietary style.")
                avoided = excluded_matches(
                    food["excluded_ingredients"], (i.name for i in recipe.ingredients)
                )
                if avoided:
                    reasons.append("Includes a food you avoid: " + ", ".join(avoided))
            if reasons:
                rows.append(
                    {
                        "day": day["day"],
                        "meal_id": meal["id"],
                        "name": meal["name"],
                        "slot": meal["slot"],
                        "recipe_id": recipe.id if recipe else meal.get("recipe_id"),
                        "status": "conflict" if recipe else "unknown",
                        "reasons": reasons,
                    }
                )
    return {"plan_revision": plan.content_version, "items": rows, "status": "checked"}


def representation(user: User) -> dict[str, Any]:
    v = values(user)
    assert user.profile is not None
    return {
        "owner": user.clerk_subject,
        "revision": user.preferences_revision,
        "values": v,
        "target": target(user),
        "restriction_review": restrictions(user, v),
        "missing": [
            k
            for k, missing in [
                ("household", v["household"] is None),
                ("food.snack_slots", v["food"]["snack_slots"] is None),
            ]
            if missing
        ],
        "age_recorded_at": (user.profile.age_recorded_at or user.profile.created_at).isoformat(),
    }


def proposal(user: User, body: PreviewRequest) -> dict[str, Any]:
    if body.expected_revision != user.preferences_revision:
        fail(
            "Your preferences changed on another device. Review your edits.",
            "preferences_conflict",
            409,
            current=representation(user),
        )
    before = values(user)
    after = merge(before, body.changes)
    validate(after)
    if after == before:
        fail("No changes to save.", "unchanged")
    # Do not erase unknown timing as a side effect of another edit.
    if (
        before["food"]["snack_slots"] is None
        and any(k in body.changes.get("food", {}) for k in ("meals_per_day", "include_breakfast"))
        and after["food"]["snack_slots"] is None
    ):
        fail("Choose snack timing when changing your meal rhythm.", fields=["food.snack_slots"])
    nutrition_changed = any(before[k] != after[k] for k in ("goal", "profile", "activity"))
    next_target = calculate(after) if nutrition_changed else target(user)
    review = restrictions(user, after)
    fingerprint = digest(
        {
            "revision": body.expected_revision,
            "changes": body.changes,
            "plan": review["plan_revision"],
            "target": next_target,
        }
    )
    return {
        "values": after,
        "before_target": target(user),
        "target": next_target,
        "nutrition_changed": nutrition_changed,
        "restriction_review": review,
        "fingerprint": fingerprint,
        "message": "Targets apply to Today after saving. Your current meals stay unchanged."
        if nutrition_changed
        else "Future plans use your routine. Current meals and shopping stay unchanged.",
    }


async def read(db: AsyncSession, subject: str) -> dict[str, Any]:
    return representation(await owned(db, subject))


async def preview(db: AsyncSession, subject: str, body: PreviewRequest) -> dict[str, Any]:
    return proposal(await owned(db, subject), body)


async def save(db: AsyncSession, subject: str, key: str, body: SaveRequest) -> dict[str, Any]:
    user = await owned(db, subject, lock=True)
    request_hash = digest(body.model_dump(mode="json"))
    receipt = await db.scalar(
        select(PreferenceReceipt).where(
            PreferenceReceipt.user_id == user.id, PreferenceReceipt.request_id == key
        )
    )
    if receipt:
        if receipt.request_hash != request_hash:
            fail("This save key belongs to a different change.", "request_reused", 409)
        return receipt.response
    draft = proposal(user, body)
    if draft["fingerprint"] != body.fingerprint:
        fail("Your plan changed. Review the current impact before saving.", "preview_changed", 409)
    # CAS complements row locks and also detects a stale writer on SQLite.
    result = await db.execute(
        update(User)
        .where(User.id == user.id, User.preferences_revision == body.expected_revision)
        .values(preferences_revision=body.expected_revision + 1)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], result).rowcount != 1:
        await db.rollback()
        fail("Your preferences changed. Review your edits.", "preferences_conflict", 409)
    before = values(user)
    v = draft["values"]
    p, g, f = user.profile, user.goal, user.food_preferences
    assert p and g and f
    p.preferred_units = v["units"]
    if before["profile"]["age"] != v["profile"]["age"]:
        p.current_age = v["profile"]["age"]
        p.age_recorded_at = datetime.now(UTC)
    p.height_cm, p.equation_sex = v["profile"]["height_cm"], v["profile"]["equation_sex"]
    if before["profile"]["weight_kg"] != v["profile"]["weight_kg"]:
        user.measurements.append(BodyMeasurement(weight_kg=v["profile"]["weight_kg"]))
    g.goal_type, g.goal_detail = v["goal"]["type"], v["goal"]["detail"]
    for name, value in v["activity"].items():
        setattr(g, name, value)
    for name, value in v["food"].items():
        setattr(f, name, value)
    if f.snack_slots is not None:
        f.include_snacks = bool(f.snack_slots)
    f.shopping_cadence = v["shopping"]["cadence"]
    if v["household"] is not None:
        h = user.planning_profile
        if h is None:
            h = PlanningProfile(schema_version=2)
            user.planning_profile = h
        h.living_arrangement = v["household"]["living_arrangement"]
        h.household_size = v["household"]["household_size"]
        h.meal_contexts = v["meal_contexts"]
        for name, value in v["shopping"].items():
            setattr(h, "shopping_cadence" if name == "cadence" else name, value)
    else:
        p.legacy_shopping = v["shopping"]
    if user.planning_profile is not None:
        p.legacy_shopping = None
    if draft["nutrition_changed"]:
        old = target(user)
        t = user.nutrition_target
        if (
            t
            and old
            and not await db.scalar(
                select(NutritionTargetHistory.id)
                .where(NutritionTargetHistory.user_id == user.id)
                .limit(1)
            )
        ):
            db.add(
                NutritionTargetHistory(
                    user_id=user.id, revision=0, target=old, effective_at=t.created_at
                )
            )
        if t is None:
            t = NutritionTarget()
            user.nutrition_target = t
        n = draft["target"]
        t.daily_energy_kcal = n["daily_energy_kcal"]
        for name, value in n["macros"].items():
            setattr(t, name, value)
        t.confidence = n["confidence"]
        t.engine_version = (
            user.starter_plan.preview_payload["engine_version"]
            if user.starter_plan
            else "preferences-v1"
        )
        t.calculation_input_hash = digest({k: v[k] for k in ("goal", "profile", "activity")})
        db.add(
            NutritionTargetHistory(
                user_id=user.id,
                revision=body.expected_revision + 1,
                verified=True,
                target={**n, "engine_version": t.engine_version},
            )
        )
    user.preferences_revision = body.expected_revision + 1
    await db.flush()
    response = representation(user)
    db.add(
        PreferenceReceipt(
            user_id=user.id, request_id=key, request_hash=request_hash, response=response
        )
    )
    await db.commit()
    return response


async def target_history(db: AsyncSession, subject: str) -> dict[str, Any]:
    user = await owned(db, subject)
    rows = (
        await db.scalars(
            select(NutritionTargetHistory)
            .where(NutritionTargetHistory.user_id == user.id)
            .order_by(NutritionTargetHistory.effective_at)
        )
    ).all()
    return {
        "items": [
            {"effective_at": r.effective_at.isoformat(), "revision": r.revision, "target": r.target}
            for r in rows
        ]
    }


async def next_plan_preview(db: AsyncSession, subject: str) -> dict[str, Any]:
    """Read-only generation from current saved settings. Never replace a plan."""
    from fuellayer.modules.onboarding.engine import build_preview

    user = await owned(db, subject)
    v = values(user)
    if v["household"] is None or v["food"]["snack_slots"] is None:
        fail(
            "Add your household and snack timing before previewing a new plan.",
            "preferences_missing",
        )
    validate(v)
    answers = OnboardingAnswersV2.model_validate({**v, "schema_version": 2, "locale": "en"})
    return build_preview(answers).model_dump(mode="json")
