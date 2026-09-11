from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator


class UnitSystem(StrEnum):
    METRIC = "metric"
    IMPERIAL = "imperial"


class GoalType(StrEnum):
    LOSE_FAT = "lose_fat"
    BUILD_MUSCLE = "build_muscle"
    MAINTAIN = "maintain"
    FUEL_PERFORMANCE = "fuel_performance"


class GoalDetail(StrEnum):
    GENTLE = "gentle"
    STEADY = "steady"
    SLOW = "slow"


class EquationSex(StrEnum):
    FEMALE = "female"
    MALE = "male"
    UNSPECIFIED = "unspecified"


class ActivityBand(StrEnum):
    MOSTLY_SEATED = "mostly_seated"
    MIXED_MOVEMENT = "mixed_movement"
    MOVING_MOST_DAY = "moving_most_day"
    HIGHLY_PHYSICAL = "highly_physical"


class TrainingFocus(StrEnum):
    NONE = "none"
    STRENGTH = "strength"
    ENDURANCE = "endurance"
    MIXED = "mixed"
    SPORT = "sport"


class DietaryPattern(StrEnum):
    NONE = "none"
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    PESCATARIAN = "pescatarian"


class AllergenCode(StrEnum):
    CELERY = "celery"
    CRUSTACEANS = "crustaceans"
    EGGS = "eggs"
    FISH = "fish"
    GLUTEN = "gluten"
    LUPIN = "lupin"
    MILK = "milk"
    MOLLUSCS = "molluscs"
    MUSTARD = "mustard"
    PEANUTS = "peanuts"
    SESAME = "sesame"
    SHELLFISH = "shellfish"
    SOY = "soy"
    SULPHITES = "sulphites"
    TREE_NUTS = "tree_nuts"


class CookingTimeBand(StrEnum):
    FIFTEEN = "15_min"
    THIRTY = "30_min"
    FORTY_FIVE_PLUS = "45_plus_min"


class ShoppingCadence(StrEnum):
    WEEKLY = "weekly"
    TWICE_MONTHLY = "twice_monthly"
    MONTHLY = "monthly"


class LivingArrangement(StrEnum):
    ALONE = "alone"
    PARTNER = "partner"
    FAMILY = "family"
    ROOMMATES = "roommates"


class MealAudience(StrEnum):
    JUST_ME = "just_me"
    SHARED = "shared"
    MIXED = "mixed"


class MealContextSlot(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"


class LocationStatus(StrEnum):
    SELECTED = "selected"
    SKIPPED = "skipped"


class LocationSource(StrEnum):
    DEVICE = "device"
    MANUAL = "manual"


class PlanStatus(StrEnum):
    READY = "ready"
    CONSTRAINED = "constrained"


class ConfidenceLevel(StrEnum):
    STANDARD = "standard"
    WIDER = "wider"


class GoalAnswers(BaseModel):
    type: GoalType
    detail: GoalDetail | None = None

    @model_validator(mode="after")
    def validate_detail(self) -> "GoalAnswers":
        allowed: dict[GoalType, set[GoalDetail | None]] = {
            GoalType.LOSE_FAT: {GoalDetail.GENTLE, GoalDetail.STEADY},
            GoalType.BUILD_MUSCLE: {GoalDetail.SLOW, GoalDetail.STEADY},
            GoalType.MAINTAIN: {None},
            GoalType.FUEL_PERFORMANCE: {None},
        }
        if self.detail not in allowed[self.type]:
            raise ValueError(f"goal detail {self.detail!s} is not valid for {self.type}")
        return self


class ProfileAnswers(BaseModel):
    age: Annotated[int, Field(ge=1, le=120)]
    height_cm: Annotated[float, Field(ge=100, le=250)]
    weight_kg: Annotated[float, Field(ge=25, le=350)]
    equation_sex: EquationSex


class ActivityAnswers(BaseModel):
    daily_movement: ActivityBand
    training_days: Annotated[int, Field(ge=0, le=7)]
    training_focus: TrainingFocus


class FoodAnswers(BaseModel):
    excluded_ingredients: list[str] = Field(default_factory=list, max_length=50)
    dietary_pattern: DietaryPattern
    allergens: list[AllergenCode] = Field(default_factory=list, max_length=15)
    meals_per_day: Annotated[int, Field(ge=2, le=4)]
    include_breakfast: bool
    include_snacks: bool
    cooking_time: CookingTimeBand
    servings: Annotated[int, Field(ge=1, le=6)]
    shopping_cadence: ShoppingCadence

    @model_validator(mode="after")
    def unique_allergens(self) -> "FoodAnswers":
        if len(self.allergens) != len(set(self.allergens)):
            raise ValueError("allergens must be unique")
        return self


class OnboardingAnswersV1(BaseModel):
    schema_version: Literal[1] = 1
    locale: Literal["en"] = "en"
    units: UnitSystem
    goal: GoalAnswers
    profile: ProfileAnswers
    activity: ActivityAnswers
    food: FoodAnswers


class HouseholdProfile(BaseModel):
    living_arrangement: LivingArrangement
    household_size: Annotated[int, Field(ge=1, le=12)]

    @model_validator(mode="after")
    def validate_size(self) -> "HouseholdProfile":
        if self.living_arrangement == LivingArrangement.ALONE and self.household_size != 1:
            raise ValueError("an alone household must have exactly one person")
        if self.living_arrangement != LivingArrangement.ALONE and self.household_size < 2:
            raise ValueError("a shared household must have at least two people")
        return self


class MealContext(BaseModel):
    slot: MealContextSlot
    audience: MealAudience
    shared_days_per_week: Annotated[int, Field(ge=0, le=7)]
    shared_servings: Annotated[int, Field(ge=1, le=12)]

    @model_validator(mode="after")
    def validate_audience_values(self) -> "MealContext":
        if self.audience == MealAudience.JUST_ME:
            if self.shared_days_per_week != 0 or self.shared_servings != 1:
                raise ValueError("just_me meals require 0 shared days and 1 serving")
        elif self.audience == MealAudience.SHARED:
            if self.shared_days_per_week != 7 or self.shared_servings < 2:
                raise ValueError("shared meals require 7 shared days and at least 2 servings")
        elif not (1 <= self.shared_days_per_week <= 6 and self.shared_servings >= 2):
            raise ValueError("mixed meals require 1-6 shared days and at least 2 servings")
        return self


class ShoppingProfile(BaseModel):
    cadence: ShoppingCadence
    location_status: LocationStatus
    location_source: LocationSource | None = None
    area_label: Annotated[str | None, Field(max_length=160)] = None
    country_code: Annotated[str | None, Field(min_length=2, max_length=2)] = None
    preferred_place_ids: list[Annotated[str, Field(min_length=1, max_length=255)]] = Field(
        default_factory=list, max_length=3
    )

    @model_validator(mode="after")
    def validate_location(self) -> "ShoppingProfile":
        if len(self.preferred_place_ids) != len(set(self.preferred_place_ids)):
            raise ValueError("preferred_place_ids must be unique")
        if self.location_status == LocationStatus.SKIPPED:
            if self.location_source is not None or self.preferred_place_ids:
                raise ValueError("skipped locations cannot include a source or stores")
        elif self.location_source is None:
            raise ValueError("selected locations require a source")
        return self


class FoodAnswersV2(BaseModel):
    excluded_ingredients: list[str] = Field(default_factory=list, max_length=50)
    dietary_pattern: DietaryPattern
    allergens: list[AllergenCode] = Field(default_factory=list, max_length=15)
    meals_per_day: Annotated[int, Field(ge=2, le=4)]
    include_breakfast: bool
    snack_slots: list[Annotated[int, Field(ge=0, le=3)]] = Field(default_factory=list, max_length=3)
    cooking_time: CookingTimeBand

    @model_validator(mode="after")
    def validate_lists(self) -> "FoodAnswersV2":
        if len(self.allergens) != len(set(self.allergens)):
            raise ValueError("allergens must be unique")
        if len(self.snack_slots) != len(set(self.snack_slots)):
            raise ValueError("snack_slots must be unique")
        if any(slot >= self.meals_per_day - 1 for slot in self.snack_slots):
            raise ValueError("snack slots must sit between configured meals")
        return self


class OnboardingAnswersV2(BaseModel):
    schema_version: Literal[2] = 2
    locale: Literal["en"] = "en"
    units: UnitSystem
    goal: GoalAnswers
    profile: ProfileAnswers
    activity: ActivityAnswers
    food: FoodAnswersV2
    household: HouseholdProfile
    meal_contexts: list[MealContext] = Field(min_length=2, max_length=3)
    shopping: ShoppingProfile

    @model_validator(mode="after")
    def validate_meal_contexts(self) -> "OnboardingAnswersV2":
        expected = {MealContextSlot.DINNER}
        if self.food.include_breakfast:
            expected.add(MealContextSlot.BREAKFAST)
        if self.food.meals_per_day - int(self.food.include_breakfast) >= 2:
            expected.add(MealContextSlot.LUNCH)
        actual = {context.slot for context in self.meal_contexts}
        if len(actual) != len(self.meal_contexts) or actual != expected:
            raise ValueError("meal_contexts must match the configured main meal slots")
        if self.household.living_arrangement == LivingArrangement.ALONE and any(
            context.audience != MealAudience.JUST_ME for context in self.meal_contexts
        ):
            raise ValueError("an alone household can only configure just_me meals")
        if any(
            context.shared_servings > self.household.household_size
            for context in self.meal_contexts
        ):
            raise ValueError("shared servings cannot exceed household size")
        return self


OnboardingAnswers = Annotated[
    OnboardingAnswersV1 | OnboardingAnswersV2,
    Field(discriminator="schema_version"),
]


class MacroTargets(BaseModel):
    protein_g: int
    carbohydrates_g: int
    fat_g: int


class IngredientAmount(BaseModel):
    name: str
    quantity: float
    unit: str
    aisle: str


class PlannedMeal(BaseModel):
    total_portions: float | None = Field(default=None, gt=0)
    portion_audience: Literal["personal", "shared"] | None = None
    portion_basis: str | None = None
    recipe_id: str | None = None
    recipe_snapshot: dict[str, Any] | None = None
    nutrition_status: Literal["estimated", "known", "missing"] = "estimated"
    id: str
    slot: str
    name: str
    description: str
    calories_kcal: int
    macros: MacroTargets
    prep_minutes: float | None
    portions: float
    ingredients: list[IngredientAmount]


class PlannedMealV2(PlannedMeal):
    audience: MealAudience
    servings: int


class PlannedDay(BaseModel):
    day: Annotated[int, Field(ge=1, le=7)]
    meals: list[PlannedMealV2]


class GroceryItem(BaseModel):
    name: str
    quantity: float
    unit: str


class GrocerySection(BaseModel):
    aisle: str
    items: list[GroceryItem]


class StarterPlanPreviewV1(BaseModel):
    start_date: str | None = None
    schema_version: Literal[1] = 1
    status: PlanStatus
    engine_version: str
    content_version: str
    input_hash: str
    daily_energy_kcal: int
    macros: MacroTargets
    confidence: ConfidenceLevel
    meals: list[PlannedMeal]
    grocery: list[GrocerySection]
    explanations: list[str]
    warnings: list[str]


class GroceryRefresh(BaseModel):
    day_offset: Annotated[int, Field(ge=7, le=21)]
    label: str
    sections: list[GrocerySection]


class GroceryPlan(BaseModel):
    horizon_days: Literal[7, 14, 28]
    main_trip: list[GrocerySection]
    fresh_refreshes: list[GroceryRefresh]


class StarterPlanPreviewV2(BaseModel):
    start_date: str | None = None
    schema_version: Literal[2] = 2
    status: PlanStatus
    engine_version: str
    content_version: str
    input_hash: str
    daily_energy_kcal: int
    macros: MacroTargets
    confidence: ConfidenceLevel
    days: list[PlannedDay] = Field(min_length=7, max_length=7)
    grocery: GroceryPlan
    explanations: list[str]
    warnings: list[str]


StarterPlanPreview = Annotated[
    StarterPlanPreviewV1 | StarterPlanPreviewV2,
    Field(discriminator="schema_version"),
]


class BootstrapUser(BaseModel):
    id: str | None
    onboarding_status: Literal["not_started", "completed"]
    planning_profile_version: Literal[1, 2] | None = None


class BootstrapResponse(BaseModel):
    user: BootstrapUser
    plan: StarterPlanPreview | None = None


class PlanningProfileUpgrade(BaseModel):
    locale: Literal["en"] = "en"
    household: HouseholdProfile
    meal_contexts: list[MealContext] = Field(min_length=2, max_length=3)
    shopping: ShoppingProfile


class CoordinateStoreSearch(BaseModel):
    source: Literal["coordinates"]
    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    locale: str = Field(default="en", min_length=2, max_length=16)
    area_label: str | None = Field(default=None, max_length=160)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)


class TextStoreSearch(BaseModel):
    source: Literal["text"]
    query: str = Field(min_length=2, max_length=160)
    locale: str = Field(default="en", min_length=2, max_length=16)


StoreSearchRequest = Annotated[
    CoordinateStoreSearch | TextStoreSearch,
    Field(discriminator="source"),
]


class NearbyStore(BaseModel):
    place_id: str
    name: str
    address: str
    distance_meters: int | None = None


class StoreSearchResponse(BaseModel):
    area_label: str
    country_code: str | None = None
    stores: list[NearbyStore]


class AccountDeletionResponse(BaseModel):
    status: Literal["deleted", "clerk_deletion_pending"]
