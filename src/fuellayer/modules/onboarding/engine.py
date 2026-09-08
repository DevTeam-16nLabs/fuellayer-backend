import hashlib
import json
from collections import defaultdict
from typing import Literal

from fuellayer.modules.onboarding.catalog import MEAL_CATALOG, CatalogMeal, PurchaseKind
from fuellayer.modules.onboarding.schemas import (
    ActivityBand,
    ConfidenceLevel,
    CookingTimeBand,
    EquationSex,
    GoalDetail,
    GoalType,
    GroceryItem,
    GroceryPlan,
    GroceryRefresh,
    GrocerySection,
    IngredientAmount,
    MacroTargets,
    MealAudience,
    MealContext,
    MealContextSlot,
    OnboardingAnswersV1,
    OnboardingAnswersV2,
    PlannedDay,
    PlannedMeal,
    PlannedMealV2,
    PlanStatus,
    StarterPlanPreviewV1,
    StarterPlanPreviewV2,
)

ENGINE_VERSION = "energy-v1.0.0"
ENGINE_VERSION_V2 = "weekly-household-v2.0.0"
CONTENT_VERSION = "starter-catalog-v1.1.0"

AnswerSet = OnboardingAnswersV1 | OnboardingAnswersV2

# These constants are intentionally centralized and versioned. They must receive
# registered-dietitian review before FuelLayer is exposed as a production product.
ACTIVITY_MULTIPLIERS = {
    ActivityBand.MOSTLY_SEATED: 1.2,
    ActivityBand.MIXED_MOVEMENT: 1.375,
    ActivityBand.MOVING_MOST_DAY: 1.55,
    ActivityBand.HIGHLY_PHYSICAL: 1.725,
}
GOAL_MULTIPLIERS = {
    (GoalType.LOSE_FAT, GoalDetail.GENTLE): 0.92,
    (GoalType.LOSE_FAT, GoalDetail.STEADY): 0.88,
    (GoalType.BUILD_MUSCLE, GoalDetail.SLOW): 1.05,
    (GoalType.BUILD_MUSCLE, GoalDetail.STEADY): 1.08,
    (GoalType.MAINTAIN, None): 1.0,
    (GoalType.FUEL_PERFORMANCE, None): 1.0,
}
PROTEIN_G_PER_KG = {
    GoalType.LOSE_FAT: 1.8,
    GoalType.BUILD_MUSCLE: 1.8,
    GoalType.MAINTAIN: 1.4,
    GoalType.FUEL_PERFORMANCE: 1.6,
}
MAX_PREP_MINUTES = {
    CookingTimeBand.FIFTEEN: 15,
    CookingTimeBand.THIRTY: 30,
    CookingTimeBand.FORTY_FIVE_PLUS: 60,
}


class UnderageNotSupportedError(ValueError):
    pass


def _canonical_hash(answers: AnswerSet) -> str:
    payload = json.dumps(
        answers.model_dump(mode="json", exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _mifflin_rmr(answers: AnswerSet) -> tuple[float, ConfidenceLevel]:
    profile = answers.profile
    base = 10 * profile.weight_kg + 6.25 * profile.height_cm - 5 * profile.age
    if profile.equation_sex == EquationSex.MALE:
        return base + 5, ConfidenceLevel.STANDARD
    if profile.equation_sex == EquationSex.FEMALE:
        return base - 161, ConfidenceLevel.STANDARD
    return ((base + 5) + (base - 161)) / 2, ConfidenceLevel.WIDER


def _energy_target(answers: AnswerSet) -> tuple[int, ConfidenceLevel, list[str]]:
    resting, confidence = _mifflin_rmr(answers)
    movement = ACTIVITY_MULTIPLIERS[answers.activity.daily_movement]
    goal_multiplier = GOAL_MULTIPLIERS[(answers.goal.type, answers.goal.detail)]
    raw_target = resting * movement * goal_multiplier
    warnings: list[str] = []

    # A goal adjustment that falls below the resting estimate is not silently used.
    if raw_target < resting:
        raw_target = resting
        warnings.append(
            "The goal adjustment reached the resting-energy estimate, so the starting "
            "target was limited."
        )
    if raw_target > 5000:
        raw_target = 5000
        warnings.append("The starting estimate reached FuelLayer's current upper review boundary.")
    return round(raw_target / 10) * 10, confidence, warnings


def _macro_targets(answers: AnswerSet, calories: int) -> MacroTargets:
    protein = round(answers.profile.weight_kg * PROTEIN_G_PER_KG[answers.goal.type])
    protein = min(protein, max(70, round(calories * 0.35 / 4)))
    fat = round(calories * 0.27 / 9)
    carbohydrates = max(0, round((calories - protein * 4 - fat * 9) / 4))
    return MacroTargets(protein_g=protein, carbohydrates_g=carbohydrates, fat_g=fat)


def _slot_plan(answers: OnboardingAnswersV1) -> list[tuple[str, str]]:
    slots: list[tuple[str, str]] = []
    if answers.food.include_breakfast:
        slots.append(("breakfast", "Breakfast"))
    slots.extend((("lunch", "Lunch"), ("dinner", "Dinner")))
    next_light_index = 1
    while len(slots) < answers.food.meals_per_day:
        if answers.food.include_snacks:
            label = "Snack" if next_light_index == 1 else f"Snack {next_light_index}"
            slots.insert(-1, ("snack", label))
        else:
            slots.insert(-1, ("lunch", f"Meal {len(slots)}"))
        next_light_index += 1
    if len(slots) > answers.food.meals_per_day:
        slots = slots[-answers.food.meals_per_day :]
    return slots


def _meal_weights(slots: list[tuple[str, str]]) -> list[float]:
    base = {"breakfast": 0.24, "lunch": 0.32, "dinner": 0.36, "snack": 0.14}
    weights = [base[slot] for slot, _ in slots]
    total = sum(weights)
    return [weight / total for weight in weights]


def _choose_meal(
    answers: AnswerSet,
    slot: str,
    input_hash: str,
    occurrence: int,
) -> CatalogMeal | None:
    allergens = set(answers.food.allergens)
    max_prep = MAX_PREP_MINUTES[answers.food.cooking_time]
    candidates = [
        meal
        for meal in MEAL_CATALOG
        if meal.slot == slot
        and answers.food.dietary_pattern in meal.patterns
        and not allergens.intersection(meal.allergens)
        and meal.prep_minutes <= max_prep
    ]
    if not candidates:
        return None
    digest = hashlib.sha256(f"{input_hash}:{slot}:{occurrence}".encode()).digest()
    return candidates[int.from_bytes(digest[:4], "big") % len(candidates)]


def _round_quantity(value: float) -> float:
    if value >= 10:
        return round(value)
    return round(value, 1)


def _build_meals(
    answers: OnboardingAnswersV1,
    input_hash: str,
    calories: int,
) -> tuple[list[PlannedMeal], list[GrocerySection]]:
    slots = _slot_plan(answers)
    weights = _meal_weights(slots)
    occurrences: dict[str, int] = defaultdict(int)
    meals: list[PlannedMeal] = []
    grocery_totals: dict[tuple[str, str, str], float] = defaultdict(float)

    for (slot, label), weight in zip(slots, weights, strict=True):
        occurrence = occurrences[slot]
        occurrences[slot] += 1
        catalog_meal = _choose_meal(answers, slot, input_hash, occurrence)
        if catalog_meal is None:
            return [], []
        target_calories = calories * weight
        scale = min(1.8, max(0.65, target_calories / catalog_meal.calories_kcal))
        meal_ingredients: list[IngredientAmount] = []
        for item in catalog_meal.ingredients:
            per_person_quantity = _round_quantity(item.quantity * scale)
            meal_ingredients.append(
                IngredientAmount(
                    name=item.name,
                    quantity=per_person_quantity,
                    unit=item.unit,
                    aisle=item.aisle,
                )
            )
            grocery_totals[(item.aisle, item.name, item.unit)] += (
                item.quantity * scale * answers.food.servings
            )
        meals.append(
            PlannedMeal(
                id=f"{catalog_meal.id}-{occurrence + 1}",
                slot=label,
                name=catalog_meal.name,
                description=catalog_meal.description,
                calories_kcal=round(catalog_meal.calories_kcal * scale),
                macros=MacroTargets(
                    protein_g=round(catalog_meal.protein_g * scale),
                    carbohydrates_g=round(catalog_meal.carbohydrates_g * scale),
                    fat_g=round(catalog_meal.fat_g * scale),
                ),
                prep_minutes=catalog_meal.prep_minutes,
                portions=round(scale, 1),
                ingredients=meal_ingredients,
            )
        )

    by_aisle: dict[str, list[GroceryItem]] = defaultdict(list)
    for (aisle, name, unit), quantity in sorted(grocery_totals.items()):
        by_aisle[aisle].append(
            GroceryItem(name=name, quantity=_round_quantity(quantity), unit=unit)
        )
    grocery = [
        GrocerySection(aisle=aisle, items=items) for aisle, items in sorted(by_aisle.items())
    ]
    return meals, grocery


def _build_preview_v1(answers: OnboardingAnswersV1) -> StarterPlanPreviewV1:
    if answers.profile.age < 18:
        raise UnderageNotSupportedError("FuelLayer currently supports adults aged 18 and over.")

    input_hash = _canonical_hash(answers)
    energy, confidence, boundary_warnings = _energy_target(answers)
    macros = _macro_targets(answers, energy)
    meals, grocery = _build_meals(answers, input_hash, energy)
    constrained = not meals

    explanations = [
        f"Built around your {answers.goal.type.value.replace('_', ' ')} goal.",
        "Matched to "
        f"{answers.activity.daily_movement.value.replace('_', ' ')} and "
        f"{answers.activity.training_days} training days each week.",
        f"Uses {answers.food.cooking_time.value.replace('_', ' ')} cooking and your "
        "selected food boundaries.",
    ]
    warnings = [
        "This is a starting estimate for general wellness, not medical nutrition advice.",
        *boundary_warnings,
    ]
    if answers.profile.equation_sex == EquationSex.UNSPECIFIED:
        warnings.append(
            "The estimate uses the midpoint of both equation constants, so its confidence "
            "range is wider."
        )
    if constrained:
        warnings.append(
            "No catalog day safely matched every selected boundary. Adjust a preference; "
            "allergies will never be ignored."
        )

    return StarterPlanPreviewV1(
        status=PlanStatus.CONSTRAINED if constrained else PlanStatus.READY,
        engine_version=ENGINE_VERSION,
        content_version=CONTENT_VERSION,
        input_hash=input_hash,
        daily_energy_kcal=energy,
        macros=macros,
        confidence=confidence,
        meals=meals,
        grocery=grocery,
        explanations=explanations,
        warnings=warnings,
    )


def _slot_plan_v2(answers: OnboardingAnswersV2) -> list[tuple[str, str, MealContextSlot | None]]:
    count = answers.food.meals_per_day
    if answers.food.include_breakfast:
        main_slots: list[tuple[str, str, MealContextSlot | None]] = [
            ("breakfast", "Breakfast", MealContextSlot.BREAKFAST)
        ]
        remaining_lunches = max(0, count - 2)
    else:
        main_slots = []
        remaining_lunches = count - 1

    for lunch_index in range(remaining_lunches):
        label = "Lunch" if lunch_index == 0 else "Afternoon meal"
        main_slots.append(("lunch", label, MealContextSlot.LUNCH))
    main_slots.append(("dinner", "Dinner", MealContextSlot.DINNER))

    slots = list(main_slots)
    for offset, insertion in enumerate(sorted(answers.food.snack_slots)):
        target = min(insertion + 1 + offset, len(slots) - 1)
        slots.insert(target, ("snack", "Snack", None))
    return slots


def _shared_days(context: MealContext) -> set[int]:
    if context.audience == MealAudience.JUST_ME:
        return set()
    if context.audience == MealAudience.SHARED:
        return set(range(7))
    # Favor the weekend, then spread the remaining shared meals across the week.
    priority = (5, 6, 2, 4, 1, 3, 0)
    return set(priority[: context.shared_days_per_week])


def _group_grocery(
    totals: dict[tuple[str, str, str], float],
) -> list[GrocerySection]:
    by_aisle: dict[str, list[GroceryItem]] = defaultdict(list)
    for (aisle, name, unit), quantity in sorted(totals.items()):
        by_aisle[aisle].append(
            GroceryItem(name=name, quantity=_round_quantity(quantity), unit=unit)
        )
    return [GrocerySection(aisle=aisle, items=items) for aisle, items in sorted(by_aisle.items())]


def _scale_totals(
    totals: dict[tuple[str, str, str], float], multiplier: int
) -> dict[tuple[str, str, str], float]:
    return {key: value * multiplier for key, value in totals.items()}


def _build_week(
    answers: OnboardingAnswersV2,
    input_hash: str,
    calories: int,
) -> tuple[
    list[PlannedDay],
    dict[tuple[str, str, str], float],
    dict[tuple[str, str, str], float],
]:
    slots = _slot_plan_v2(answers)
    weights = _meal_weights([(slot, label) for slot, label, _ in slots])
    contexts = {context.slot: context for context in answers.meal_contexts}
    shared_day_sets = {slot: _shared_days(context) for slot, context in contexts.items()}
    fresh_totals: dict[tuple[str, str, str], float] = defaultdict(float)
    long_life_totals: dict[tuple[str, str, str], float] = defaultdict(float)
    days: list[PlannedDay] = []

    for day_index in range(7):
        planned_meals: list[PlannedMealV2] = []
        for slot_index, ((slot, label, context_slot), weight) in enumerate(
            zip(slots, weights, strict=True)
        ):
            occurrence = day_index * len(slots) + slot_index
            catalog_meal = _choose_meal(answers, slot, input_hash, occurrence)
            if catalog_meal is None:
                return [PlannedDay(day=day, meals=[]) for day in range(1, 8)], {}, {}

            target_calories = calories * weight
            user_scale = min(1.8, max(0.65, target_calories / catalog_meal.calories_kcal))
            context = contexts.get(context_slot) if context_slot else None
            shared = bool(context and day_index in shared_day_sets[context.slot])
            servings = context.shared_servings if context and shared else 1
            audience = MealAudience.SHARED if shared else MealAudience.JUST_ME
            meal_ingredients: list[IngredientAmount] = []

            for item in catalog_meal.ingredients:
                user_quantity = _round_quantity(item.quantity * user_scale)
                meal_ingredients.append(
                    IngredientAmount(
                        name=item.name,
                        quantity=user_quantity,
                        unit=item.unit,
                        aisle=item.aisle,
                    )
                )
                grocery_quantity = item.quantity * (user_scale + max(0, servings - 1))
                target_totals = (
                    fresh_totals if item.purchase_kind == PurchaseKind.FRESH else long_life_totals
                )
                target_totals[(item.aisle, item.name, item.unit)] += grocery_quantity

            planned_meals.append(
                PlannedMealV2(
                    id=f"day-{day_index + 1}-{catalog_meal.id}-{slot_index + 1}",
                    slot=label,
                    name=catalog_meal.name,
                    description=catalog_meal.description,
                    calories_kcal=round(catalog_meal.calories_kcal * user_scale),
                    macros=MacroTargets(
                        protein_g=round(catalog_meal.protein_g * user_scale),
                        carbohydrates_g=round(catalog_meal.carbohydrates_g * user_scale),
                        fat_g=round(catalog_meal.fat_g * user_scale),
                    ),
                    prep_minutes=catalog_meal.prep_minutes,
                    portions=round(user_scale, 1),
                    ingredients=meal_ingredients,
                    audience=audience,
                    servings=servings,
                )
            )
        days.append(PlannedDay(day=day_index + 1, meals=planned_meals))
    return days, fresh_totals, long_life_totals


def _build_preview_v2(answers: OnboardingAnswersV2) -> StarterPlanPreviewV2:
    if answers.profile.age < 18:
        raise UnderageNotSupportedError("FuelLayer currently supports adults aged 18 and over.")

    input_hash = _canonical_hash(answers)
    energy, confidence, boundary_warnings = _energy_target(answers)
    macros = _macro_targets(answers, energy)
    days, fresh_week, long_life_week = _build_week(answers, input_hash, energy)
    constrained = any(not day.meals for day in days)
    horizon_days: Literal[7, 14, 28]
    if answers.shopping.cadence.value == "weekly":
        horizon_days = 7
    elif answers.shopping.cadence.value == "twice_monthly":
        horizon_days = 14
    else:
        horizon_days = 28
    weeks = horizon_days // 7

    main_totals = _scale_totals(long_life_week, weeks)
    for key, value in fresh_week.items():
        main_totals[key] = main_totals.get(key, 0) + value
    refresh_sections = _group_grocery(fresh_week)
    fresh_refreshes = [
        GroceryRefresh(
            day_offset=week * 7,
            label=f"Fresh food refresh · week {week + 1}",
            sections=refresh_sections,
        )
        for week in range(1, weeks)
        if refresh_sections
    ]

    explanations = [
        f"Built around your {answers.goal.type.value.replace('_', ' ')} goal.",
        "Uses a seven-day menu and your real mix of personal and shared meals.",
        f"Your grocery plan covers {horizon_days} days and separates fresh refills.",
    ]
    warnings = [
        "This is a starting estimate for general wellness, not medical nutrition advice.",
        *boundary_warnings,
    ]
    if answers.profile.equation_sex == EquationSex.UNSPECIFIED:
        warnings.append(
            "The estimate uses the midpoint of both equation constants, so its confidence "
            "range is wider."
        )
    if constrained:
        warnings.append(
            "No catalog week safely matched every selected boundary. Adjust a preference; "
            "allergies will never be ignored."
        )

    return StarterPlanPreviewV2(
        status=PlanStatus.CONSTRAINED if constrained else PlanStatus.READY,
        engine_version=ENGINE_VERSION_V2,
        content_version=CONTENT_VERSION,
        input_hash=input_hash,
        daily_energy_kcal=energy,
        macros=macros,
        confidence=confidence,
        days=days,
        grocery=GroceryPlan(
            horizon_days=horizon_days,
            main_trip=[] if constrained else _group_grocery(main_totals),
            fresh_refreshes=[] if constrained else fresh_refreshes,
        ),
        explanations=explanations,
        warnings=warnings,
    )


def build_preview(
    answers: OnboardingAnswersV1 | OnboardingAnswersV2,
) -> StarterPlanPreviewV1 | StarterPlanPreviewV2:
    if isinstance(answers, OnboardingAnswersV2):
        return _build_preview_v2(answers)
    return _build_preview_v1(answers)
