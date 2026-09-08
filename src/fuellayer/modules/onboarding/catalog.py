from dataclasses import dataclass
from enum import StrEnum

from fuellayer.modules.onboarding.schemas import AllergenCode, DietaryPattern


class PurchaseKind(StrEnum):
    FRESH = "fresh"
    LONG_LIFE = "long_life"


@dataclass(frozen=True)
class CatalogIngredient:
    name: str
    quantity: float
    unit: str
    aisle: str
    purchase_kind: PurchaseKind


@dataclass(frozen=True)
class CatalogMeal:
    id: str
    slot: str
    name: str
    description: str
    calories_kcal: int
    protein_g: int
    carbohydrates_g: int
    fat_g: int
    prep_minutes: int
    patterns: frozenset[DietaryPattern]
    allergens: frozenset[AllergenCode]
    ingredients: tuple[CatalogIngredient, ...]


VEGAN = frozenset(
    {
        DietaryPattern.NONE,
        DietaryPattern.VEGETARIAN,
        DietaryPattern.VEGAN,
        DietaryPattern.PESCATARIAN,
    }
)
VEGETARIAN = frozenset({DietaryPattern.NONE, DietaryPattern.VEGETARIAN, DietaryPattern.PESCATARIAN})
PESCATARIAN = frozenset({DietaryPattern.NONE, DietaryPattern.PESCATARIAN})
OMNIVORE = frozenset({DietaryPattern.NONE})


def ingredient(name: str, quantity: float, unit: str, aisle: str) -> CatalogIngredient:
    fresh_aisles = {"Produce", "Dairy & Eggs", "Meat & Fish"}
    return CatalogIngredient(
        name=name,
        quantity=quantity,
        unit=unit,
        aisle=aisle,
        purchase_kind=PurchaseKind.FRESH if aisle in fresh_aisles else PurchaseKind.LONG_LIFE,
    )


MEAL_CATALOG: tuple[CatalogMeal, ...] = (
    CatalogMeal(
        "breakfast-chia-fruit",
        "breakfast",
        "Chia fruit bowl",
        "Chia, banana and berries with a bright citrus finish.",
        410,
        14,
        58,
        15,
        8,
        VEGAN,
        frozenset(),
        (
            ingredient("chia seeds", 35, "g", "Pantry"),
            ingredient("banana", 1, "item", "Produce"),
            ingredient("mixed berries", 140, "g", "Produce"),
            ingredient("orange", 1, "item", "Produce"),
        ),
    ),
    CatalogMeal(
        "breakfast-savory-potato",
        "breakfast",
        "Savory potato breakfast",
        "Crisp potato, spinach and tomatoes with warm spices.",
        430,
        13,
        69,
        12,
        15,
        VEGAN,
        frozenset(),
        (
            ingredient("potatoes", 280, "g", "Produce"),
            ingredient("baby spinach", 80, "g", "Produce"),
            ingredient("cherry tomatoes", 120, "g", "Produce"),
            ingredient("olive oil", 12, "ml", "Pantry"),
        ),
    ),
    CatalogMeal(
        "breakfast-eggs-potato",
        "breakfast",
        "Eggs and herbed potatoes",
        "Soft eggs, herbed potatoes and wilted greens.",
        455,
        24,
        48,
        19,
        18,
        VEGETARIAN,
        frozenset({AllergenCode.EGGS}),
        (
            ingredient("eggs", 3, "item", "Dairy & Eggs"),
            ingredient("potatoes", 220, "g", "Produce"),
            ingredient("baby spinach", 70, "g", "Produce"),
        ),
    ),
    CatalogMeal(
        "breakfast-yogurt-berries",
        "breakfast",
        "Yogurt berry crunch",
        "Thick yogurt, berries and toasted oat crunch.",
        400,
        27,
        52,
        10,
        5,
        VEGETARIAN,
        frozenset({AllergenCode.MILK, AllergenCode.GLUTEN}),
        (
            ingredient("Greek yogurt", 250, "g", "Dairy & Eggs"),
            ingredient("mixed berries", 150, "g", "Produce"),
            ingredient("rolled oats", 45, "g", "Pantry"),
        ),
    ),
    CatalogMeal(
        "lunch-quinoa-chickpea",
        "lunch",
        "Lemon chickpea quinoa",
        "A crisp quinoa bowl with chickpeas, cucumber and lemon.",
        590,
        23,
        88,
        16,
        18,
        VEGAN,
        frozenset(),
        (
            ingredient("quinoa", 90, "g", "Pantry"),
            ingredient("chickpeas", 180, "g", "Canned Goods"),
            ingredient("cucumber", 1, "item", "Produce"),
            ingredient("lemon", 1, "item", "Produce"),
            ingredient("parsley", 20, "g", "Produce"),
        ),
    ),
    CatalogMeal(
        "lunch-lentil-tomato",
        "lunch",
        "Smoky lentil tomato bowl",
        "Lentils, roasted peppers and rice with smoked paprika.",
        610,
        28,
        104,
        11,
        24,
        VEGAN,
        frozenset(),
        (
            ingredient("brown lentils", 190, "g", "Canned Goods"),
            ingredient("brown rice", 85, "g", "Pantry"),
            ingredient("red pepper", 1, "item", "Produce"),
            ingredient("chopped tomatoes", 200, "g", "Canned Goods"),
        ),
    ),
    CatalogMeal(
        "lunch-tofu-rice",
        "lunch",
        "Ginger tofu rice bowl",
        "Golden tofu, green beans and rice with fresh ginger.",
        620,
        32,
        86,
        18,
        22,
        VEGAN,
        frozenset({AllergenCode.SOY}),
        (
            ingredient("firm tofu", 220, "g", "Refrigerated"),
            ingredient("jasmine rice", 90, "g", "Pantry"),
            ingredient("green beans", 160, "g", "Produce"),
            ingredient("fresh ginger", 15, "g", "Produce"),
        ),
    ),
    CatalogMeal(
        "lunch-tuna-potato",
        "lunch",
        "Tuna and warm potato salad",
        "Tuna, warm potatoes, greens and a sharp lemon dressing.",
        575,
        42,
        65,
        16,
        20,
        PESCATARIAN,
        frozenset({AllergenCode.FISH}),
        (
            ingredient("tuna", 160, "g", "Canned Goods"),
            ingredient("potatoes", 300, "g", "Produce"),
            ingredient("mixed greens", 100, "g", "Produce"),
            ingredient("lemon", 1, "item", "Produce"),
        ),
    ),
    CatalogMeal(
        "lunch-chicken-rice",
        "lunch",
        "Paprika chicken rice bowl",
        "Paprika chicken, rice and crunchy vegetables.",
        640,
        48,
        79,
        15,
        22,
        OMNIVORE,
        frozenset(),
        (
            ingredient("chicken breast", 190, "g", "Meat & Fish"),
            ingredient("basmati rice", 90, "g", "Pantry"),
            ingredient("carrots", 2, "item", "Produce"),
            ingredient("cucumber", 1, "item", "Produce"),
        ),
    ),
    CatalogMeal(
        "dinner-bean-sweet-potato",
        "dinner",
        "Black bean sweet potato plate",
        "Roasted sweet potato, black beans, corn and lime.",
        680,
        25,
        116,
        16,
        28,
        VEGAN,
        frozenset(),
        (
            ingredient("sweet potatoes", 350, "g", "Produce"),
            ingredient("black beans", 200, "g", "Canned Goods"),
            ingredient("corn", 120, "g", "Frozen"),
            ingredient("lime", 1, "item", "Produce"),
        ),
    ),
    CatalogMeal(
        "dinner-coconut-lentils",
        "dinner",
        "Coconut red lentils",
        "Creamy red lentils with spinach and fragrant rice.",
        700,
        26,
        105,
        22,
        30,
        VEGAN,
        frozenset(),
        (
            ingredient("red lentils", 110, "g", "Pantry"),
            ingredient("coconut milk", 160, "ml", "Canned Goods"),
            ingredient("basmati rice", 80, "g", "Pantry"),
            ingredient("baby spinach", 100, "g", "Produce"),
        ),
    ),
    CatalogMeal(
        "dinner-salmon-quinoa",
        "dinner",
        "Roasted salmon quinoa",
        "Roasted salmon, quinoa and lemony greens.",
        690,
        49,
        67,
        25,
        26,
        PESCATARIAN,
        frozenset({AllergenCode.FISH}),
        (
            ingredient("salmon fillet", 190, "g", "Meat & Fish"),
            ingredient("quinoa", 90, "g", "Pantry"),
            ingredient("broccoli", 220, "g", "Produce"),
            ingredient("lemon", 1, "item", "Produce"),
        ),
    ),
    CatalogMeal(
        "dinner-turkey-tomato-pasta",
        "dinner",
        "Turkey tomato pasta",
        "Lean turkey, tomato and herbs folded through pasta.",
        720,
        52,
        94,
        17,
        28,
        OMNIVORE,
        frozenset({AllergenCode.GLUTEN}),
        (
            ingredient("lean turkey mince", 190, "g", "Meat & Fish"),
            ingredient("pasta", 110, "g", "Pantry"),
            ingredient("chopped tomatoes", 250, "g", "Canned Goods"),
            ingredient("basil", 15, "g", "Produce"),
        ),
    ),
    CatalogMeal(
        "snack-fruit-seed",
        "snack",
        "Fruit and seed snack",
        "Fresh fruit with roasted pumpkin seeds.",
        260,
        9,
        35,
        11,
        3,
        VEGAN,
        frozenset(),
        (
            ingredient("apple", 1, "item", "Produce"),
            ingredient("banana", 1, "item", "Produce"),
            ingredient("pumpkin seeds", 25, "g", "Pantry"),
        ),
    ),
    CatalogMeal(
        "snack-yogurt-banana",
        "snack",
        "Yogurt and banana",
        "Plain yogurt, banana and cinnamon.",
        245,
        18,
        35,
        5,
        3,
        VEGETARIAN,
        frozenset({AllergenCode.MILK}),
        (
            ingredient("Greek yogurt", 180, "g", "Dairy & Eggs"),
            ingredient("banana", 1, "item", "Produce"),
        ),
    ),
    CatalogMeal(
        "snack-hummus-veg",
        "snack",
        "Hummus and crisp vegetables",
        "Creamy hummus with cucumber and carrots.",
        250,
        10,
        34,
        10,
        5,
        VEGAN,
        frozenset({AllergenCode.SESAME}),
        (
            ingredient("hummus", 100, "g", "Refrigerated"),
            ingredient("cucumber", 1, "item", "Produce"),
            ingredient("carrots", 2, "item", "Produce"),
        ),
    ),
)
