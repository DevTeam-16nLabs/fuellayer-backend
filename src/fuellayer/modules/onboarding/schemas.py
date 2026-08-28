from enum import StrEnum
from typing import Annotated, Literal

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
    id: str
    slot: str
    name: str
    description: str
    calories_kcal: int
    macros: MacroTargets
    prep_minutes: int
    portions: float
    ingredients: list[IngredientAmount]


class GroceryItem(BaseModel):
    name: str
    quantity: float
    unit: str


class GrocerySection(BaseModel):
    aisle: str
    items: list[GroceryItem]


class StarterPlanPreviewV1(BaseModel):
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


class BootstrapUser(BaseModel):
    id: str | None
    onboarding_status: Literal["not_started", "completed"]


class BootstrapResponse(BaseModel):
    user: BootstrapUser
    plan: StarterPlanPreviewV1 | None = None


class AccountDeletionResponse(BaseModel):
    status: Literal["deleted", "clerk_deletion_pending"]
