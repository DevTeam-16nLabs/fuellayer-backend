"""Read-only proposals and atomic, account-serialized single-occurrence replacement."""

import copy
import hashlib
import json
import math
import uuid
from typing import Any, NoReturn, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.modules.courses.models import CoursesLedger
from fuellayer.modules.courses.service import reconcile
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.onboarding.catalog import MEAL_CATALOG
from fuellayer.modules.onboarding.models import GroceryListRecord, User
from fuellayer.modules.plan.models import MealReplacement
from fuellayer.modules.plan.schemas import (
    ConfirmRequest,
    Occurrence,
    PortionConfirmRequest,
    PortionPreviewRequest,
    PreviewRequest,
)
from fuellayer.modules.preferences.constraints import allergy_matches, excluded_matches
from fuellayer.modules.recipes import resolver
from fuellayer.modules.recipes.service import get_library, get_user

CATALOG = {meal.id: meal for meal in MEAL_CATALOG}
EMPTY: dict[str, Any] = {"items": [], "receipts": [], "transfers": [], "plan_version": None}
NUTRIENTS = ("calories_kcal", "protein_g", "carbohydrates_g", "fat_g")


def fail(message: str, code: str = "replacement_conflict", status: int = 409) -> NoReturn:
    raise HTTPException(status, detail={"code": code, "message": message})


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def meals_for(payload: dict[str, Any], offset: int) -> list[dict[str, Any]]:
    if payload["schema_version"] == 1:
        return payload["meals"] if offset == 0 else []
    return next((d["meals"] for d in payload["days"] if d["day"] == offset + 1), [])


def locate(user: User, body: Occurrence) -> tuple[dict[str, Any], dict[str, Any], int]:
    record = user.starter_plan
    if record is None:
        fail("Your saved plan is unavailable.", status=404)
    payload = record.preview_payload
    if record.content_version != body.plan_revision:
        fail("Your plan changed. Reload it before choosing a replacement.", "plan_changed")
    anchor = payload.get("start_date")
    if anchor and anchor != body.start_date.isoformat():
        fail("Your plan dates changed. Reload your plan.", "plan_changed")
    offset = (body.date - body.start_date).days
    current = next((m for m in meals_for(payload, offset) if m["id"] == body.meal_id), None)
    if current is None:
        fail("This planned meal is no longer available. Reload your plan.", "plan_changed")
    if not isinstance(current.get("portions"), (int, float)) or current["portions"] <= 0:
        fail("The personal portion could not be verified.", "portion_unknown")
    return payload, current, offset


def catalogue_id(meal: dict[str, Any]) -> str | None:
    # Legacy plans predate recipe IDs. Exact catalogue-name matching only.
    return meal.get("recipe_id") or next(
        (item.id for item in MEAL_CATALOG if item.name == meal["name"]), None
    )


def servings(user: User, meal: dict[str, Any]) -> int:
    if "audience" in meal:
        return int(meal["servings"]) if meal["audience"] == "shared" else 1
    if user.food_preferences is None:
        fail("The meal's shared portions could not be verified.", "portion_unknown")
    return user.food_preferences.servings


def nutrients(meal: dict[str, Any]) -> dict[str, float | None]:
    return {"calories_kcal": meal.get("calories_kcal"), **meal.get("macros", {})}


def totals(meals: list[dict[str, Any]]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for key in NUTRIENTS:
        values = [nutrients(m).get(key) for m in meals]
        result[key] = (
            None
            if any(v is None for v in values)
            else round(sum(v for v in values if v is not None), 2)
        )
    return result


def household(user: User, meal: dict[str, Any]) -> list[dict[str, Any]]:
    recipe = resolver.for_meal(user, meal)
    if recipe is None:
        fail("This meal's ingredient basis could not be verified.", "ingredients_unknown")
    if meal.get("total_portions") is not None:
        total = meal["total_portions"]
        if (
            not isinstance(total, (int, float))
            or not math.isfinite(total)
            or total < meal["portions"]
        ):
            fail("The total portions could not be verified.", "portion_unknown")
        return [
            {
                "name": i.name,
                "quantity": round(i.quantity * total, 2),
                "unit": i.unit,
                "aisle": i.aisle,
            }
            for i in recipe.ingredients
        ]
    others = servings(user, meal) - 1
    base = {(i.name, i.unit): i for i in recipe.ingredients}
    rows = []
    for item in meal["ingredients"]:
        match = base.get((item["name"], item["unit"]))
        if match is None or item.get("quantity") is None:
            fail("Ingredient amounts could not be verified.", "ingredients_unknown")
        rows.append({**item, "quantity": round(item["quantity"] + match.quantity * others, 2)})
    return rows


def scaled(
    current: dict[str, Any],
    recipe_id: str,
    user: User,
    resolved: resolver.ResolvedRecipe | None = None,
) -> dict[str, Any]:
    recipe = resolved or resolver.catalogue(recipe_id)
    preferences = user.food_preferences
    if recipe is None:
        fail("This recipe is no longer available.", "recipe_missing", 404)
    if preferences is None:
        fail("Your dietary and allergy information is unavailable.", "compatibility_unknown")
    if allergy_matches(preferences.allergens, recipe.allergens):
        fail("This recipe conflicts with your saved allergies.", "allergy_conflict")
    if excluded_matches(
        preferences.excluded_ingredients or [], (i.name for i in recipe.ingredients)
    ):
        fail("This recipe includes a food you avoid.", "exclusion_conflict")
    if preferences.dietary_pattern not in recipe.patterns:
        fail("This recipe does not match your dietary preferences.", "dietary_conflict")
    if catalogue_id(current) == recipe_id:
        fail("Choose a different recipe.", "same_recipe")
    scale = current["portions"]
    return {
        **current,
        "recipe_id": recipe_id,
        **(
            {"portion_basis": digest(recipe.snapshot)}
            if current.get("total_portions") is not None
            else {}
        ),
        "recipe_snapshot": recipe.snapshot if recipe_id.startswith("import:") else None,
        "name": recipe.name,
        "description": recipe.description,
        "calories_kcal": round(recipe.calories_kcal * scale),
        "macros": {
            "protein_g": round(recipe.protein_g * scale),
            "carbohydrates_g": round(recipe.carbohydrates_g * scale),
            "fat_g": round(recipe.fat_g * scale),
        },
        "prep_minutes": recipe.prep_minutes,
        "nutrition_status": recipe.nutrition_status,
        "ingredients": [
            {
                "name": i.name,
                "quantity": round(i.quantity * scale, 2),
                "unit": i.unit,
                "aisle": i.aisle,
            }
            for i in recipe.ingredients
        ],
    }


def audience(user: User, meal: dict[str, Any]) -> str:
    stored = meal.get("portion_audience") or meal.get("audience")
    if stored:
        return "shared" if stored == "shared" else "personal"
    return "shared" if servings(user, meal) > 1 else "personal"


def total_portions(user: User, meal: dict[str, Any]) -> float:
    recipe = resolver.for_meal(user, meal)
    if recipe is None:
        fail("This meal's standard portion is unavailable.", "portion_unknown")
    if meal.get("total_portions") is not None:
        if meal.get("portion_basis") and meal["portion_basis"] != digest(recipe.snapshot):
            fail(
                "This recipe’s standard portion changed. Reload meal details before adjusting.",
                "portion_unknown",
            )
        household(user, meal)  # Validate explicit amount before using it.
        return float(meal["total_portions"])
    base = {(i.name, i.unit): i for i in recipe.ingredients}
    items = meal.get("ingredients", [])
    if (len(items) != len(base) or {(i["name"], i["unit"]) for i in items} != base.keys()) or any(
        (match := base.get((i["name"], i["unit"]))) is None
        or not isinstance(i.get("quantity"), (int, float))
        or not math.isclose(i["quantity"], match.quantity * meal["portions"], abs_tol=0.02)
        for i in items
    ):
        fail(
            "The recipe's portion basis could not be verified. Reload meal details.",
            "portion_unknown",
        )
    return float(round(meal["portions"] + servings(user, meal) - 1, 2))


def adjusted(user: User, current: dict[str, Any], body: PortionPreviewRequest) -> dict[str, Any]:
    total_portions(user, current)
    recipe_id = catalogue_id(current)
    recipe = resolver.for_meal(user, current)
    if recipe is None:
        fail("This recipe is no longer available.", "recipe_missing", 404)
    assert recipe_id is not None
    preferences = user.food_preferences
    if preferences is None:
        fail("Your allergy information is unavailable. Reload your plan.", "compatibility_unknown")
    if allergy_matches(preferences.allergens, recipe.allergens):
        fail("This meal conflicts with your saved allergies.", "allergy_conflict")
    personal = float(body.personal_portions)
    result = {
        **current,
        "recipe_id": recipe_id,
        "portions": personal,
        "total_portions": float(body.total_portions),
        "portion_audience": body.audience,
        "portion_basis": digest(recipe.snapshot),
        "audience": "shared" if body.audience == "shared" else "just_me",
    }
    if personal != current["portions"]:
        result.update(
            calories_kcal=round(recipe.calories_kcal * personal),
            nutrition_status=recipe.nutrition_status,
            macros={
                "protein_g": round(recipe.protein_g * personal),
                "carbohydrates_g": round(recipe.carbohydrates_g * personal),
                "fat_g": round(recipe.fat_g * personal),
            },
            ingredients=[
                {
                    "name": i.name,
                    "quantity": round(i.quantity * personal, 2),
                    "unit": i.unit,
                    "aisle": i.aisle,
                }
                for i in recipe.ingredients
            ],
        )
    return result


async def portion_context(session: AsyncSession, subject: str, body: Occurrence) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    _, current, _ = locate(user, body)
    total = total_portions(user, current)
    return {
        "current": current,
        "personal_portions": current["portions"],
        "total_portions": total,
        "audience": audience(user, current),
        "minimum": 0.25,
        "maximum": 20,
        "step": 0.25,
        "basis": "1 standard recipe portion",
        "start_date": body.start_date.isoformat(),
    }


def converted(quantity: float, source: str | None, target: str) -> float | None:
    # Mass-to-mass and volume-to-volume only. No can sizes, density or cooked/raw guesses.
    units = {
        "g": ("mass", 1),
        "kg": ("mass", 1000),
        "ml": ("volume", 1),
        "l": ("volume", 1000),
        "item": ("count", 1),
        "items": ("count", 1),
        "pièce": ("count", 1),
        "pièces": ("count", 1),
    }
    a, b = units.get((source or "").lower()), units.get(target.lower())
    if source == target:
        return quantity
    if a and b and a[0] == b[0]:
        return quantity * a[1] / b[1]
    return None


def coverage(items: list[dict[str, Any]], lots: list[KitchenItem]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        matches = [
            lot
            for lot in lots
            if lot.status == "active" and lot.ingredient_key == item["name"] and lot.quantity != 0
        ]
        values = [
            converted(lot.quantity, lot.unit, item["unit"]) if lot.quantity is not None else None
            for lot in matches
        ]
        unknown = any(v is None for v in values)
        known = round(sum(v for v in values if v is not None), 2)
        enough = known >= item["quantity"]
        rows.append(
            {
                **item,
                "present": bool(matches),
                "at_home": None if unknown else known,
                "known_at_home": known,
                "shortage": 0
                if enough
                else None
                if unknown
                else round(max(0, item["quantity"] - known), 2),
                "coverage": "enough" if enough else "unknown" if unknown else "short",
            }
        )
    return rows


def flatten(groups: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        json.dumps([s["aisle"], i["name"], i["unit"]]): {**i, "aisle": s["aisle"]}
        for s in groups
        for i in s["items"]
    }


def group(rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(rows):
        row = rows[key]
        if row["quantity"] > 0:
            groups.setdefault(row["aisle"], []).append(
                {k: row[k] for k in ("name", "quantity", "unit")}
            )
    return [{"aisle": aisle, "items": items} for aisle, items in groups.items()]


def raw_groups(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        plan["grocery"] if plan["schema_version"] == 1 else plan["grocery"]["main_trip"],
    )


def changed_groups(
    before: list[dict[str, Any]], old: list[dict[str, Any]], new: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], set[str]]:
    rows = flatten(before)
    touched = set()
    for sign, items in ((-1, old), (1, new)):
        for item in items:
            key = json.dumps([item["aisle"], item["name"], item["unit"]])
            touched.add(key)
            previous = rows.get(key, {**item, "quantity": 0})
            rows[key] = {
                **previous,
                "quantity": round(max(0, previous["quantity"] + sign * item["quantity"]), 2),
            }
    return group(rows), touched


def reconcile_copy(
    data: dict[str, Any], groups: list[dict[str, Any]], version: str, owner: uuid.UUID
) -> dict[str, Any]:
    result = copy.deepcopy(data)
    prior_ids = {i["id"] for i in result["items"]}
    # A manually added item with the same exact name/unit owns its amount too.
    manual = {(i["name"].casefold(), i["unit"]) for i in result["items"] if not i.get("source_key")}
    groups = [
        {**g, "items": [i for i in g["items"] if (i["name"].casefold(), i["unit"]) not in manual]}
        for g in groups
    ]
    reconcile(result, groups, version)
    for item in result["items"]:
        if item["id"] not in prior_ids:
            item["id"] = str(uuid.uuid5(owner, f"{version}:{item['source_key']}"))
    return result


def shopping_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    left = {i["id"]: i for i in before["items"]}
    right = {i["id"]: i for i in after["items"]}
    changes = []
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        protected = a and (
            a["status"] != "to_buy" or a.get("overridden") or not a.get("source_key")
        )
        if a == b and not protected:
            continue
        assert a is not None or b is not None
        changes.append(
            {
                "id": key,
                "name": (b if b is not None else a or {})["name"],
                "unit": (b if b is not None else a or {})["unit"],
                "before": a,
                "after": b,
                "kind": "kept"
                if a == b
                else "added"
                if a is None
                else "removed"
                if b is None
                else "updated",
                "reason": "Purchased, checked or manually changed"
                if protected
                else "Updated requirement for this meal",
            }
        )
    return changes


async def context(session: AsyncSession, subject: str, body: Occurrence) -> dict[str, Any]:
    user = await get_user(session, subject)
    _, current, _ = locate(user, body)
    library = await get_library(session, subject)
    max_prep = {"15_min": 15, "30_min": 30}.get(
        user.food_preferences.cooking_time if user.food_preferences else ""
    )
    allowed = [
        r
        for r in library.items
        if r.compatibility == "matches"
        and (max_prep is None or (r.prep_minutes is not None and r.prep_minutes <= max_prep))
        and r.id != catalogue_id(current)
        and (r.capabilities is None or r.capabilities.get("replace", {}).get("allowed"))
    ]
    count = servings(user, current)
    return {
        "current": current,
        "cooking_servings": count,
        "personal_portions": current["portions"],
        "start_date": body.start_date.isoformat(),
        "dietary_pattern": user.food_preferences.dietary_pattern if user.food_preferences else None,
        "allergens": user.food_preferences.allergens if user.food_preferences else [],
        "compatibility_note": "Preferences and allergy information are unavailable."
        if user.food_preferences is None
        else "Checked against your saved preferences. Other diners not checked."
        if count > 1
        else "Checked against your saved preferences.",
        "excluded_count": sum(r.compatibility != "matches" for r in library.items),
        "saved_excluded_count": sum(
            r.saved and r.compatibility != "matches" for r in library.items
        ),
        "items": [r.model_dump(mode="json") for r in allowed],
        "inventory_status": library.inventory_status,
    }


async def proposal(
    session: AsyncSession, user: User, body: PreviewRequest | PortionPreviewRequest
) -> dict[str, Any]:
    plan, current, offset = locate(user, body)
    adjustment = isinstance(body, PortionPreviewRequest)
    replacement = (
        adjusted(user, current, body)
        if isinstance(body, PortionPreviewRequest)
        else scaled(
            current, body.recipe_id, user, await resolver.resolve(session, user, body.recipe_id)
        )
    )
    recipe_id = catalogue_id(replacement)
    assert recipe_id is not None
    assert user.food_preferences is not None
    lots = list(
        await session.scalars(
            select(KitchenItem).where(KitchenItem.user_id == user.id).order_by(KitchenItem.id)
        )
    )
    ledger = await session.get(CoursesLedger, user.id)
    grocery = await session.scalar(
        select(GroceryListRecord).where(GroceryListRecord.user_id == user.id)
    )
    if grocery is None:
        fail("Shopping requirements are unavailable. Reload your plan.", "shopping_unavailable")
    before_raw = raw_groups(plan)
    old, new = household(user, current), household(user, replacement)
    if adjustment:
        # Personal nutrition and audience-only edits must not re-net shopping amounts.
        left = {(i["name"], i["unit"]): i for i in old}
        right = {(i["name"], i["unit"]): i for i in new}
        changed = {k for k in left.keys() | right.keys() if left.get(k) != right.get(k)}
        old_diff = [i for k, i in left.items() if k in changed]
        new_diff = [i for k, i in right.items() if k in changed]
    else:
        old_diff, new_diff = old, new
    after_raw, touched = changed_groups(before_raw, old_diff, new_diff)
    before_data = reconcile_copy(
        ledger.data if ledger else EMPTY, grocery.grouped_items, grocery.content_version, user.id
    )
    # Recompute net quantities ONLY for ingredients changed by this occurrence.
    # Other days remain in aggregate requirements; fresh refills remain unchanged.
    net = flatten(after_raw)
    for row in coverage([r for k, r in net.items() if k in touched], lots):
        key = json.dumps([row["aisle"], row["name"], row["unit"]])
        if row["shortage"] is not None:
            net[key]["quantity"] = row["shortage"]
    # Keep unrelated net requirements, including amounts already covered by stock.
    previous_net = flatten(grocery.grouped_items)
    for key in (net.keys() | previous_net.keys()) - touched:
        if key in previous_net:
            net[key] = previous_net[key]
        else:
            net.pop(key, None)
    replacement_recipe = resolver.for_meal(user, replacement)
    assert replacement_recipe is not None
    fingerprint = digest(
        {
            "plan": plan,
            "recipe": replacement_recipe.snapshot,
            "grocery": grocery.grouped_items,
            "ledger": ledger.data if ledger else EMPTY,
            "ledger_version": ledger.version if ledger else 0,
            "stock": [
                (str(lot.id), lot.version, lot.status, lot.ingredient_key, lot.quantity, lot.unit)
                for lot in lots
            ],
            "preferences": [
                user.food_preferences.dietary_pattern,
                user.food_preferences.allergens,
                user.food_preferences.excluded_ingredients or [],
                user.preferences_revision,
            ],
            "body": body.model_dump(mode="json"),
        }
    )
    after_data = reconcile_copy(before_data, group(net), fingerprint, user.id)
    changed_plan = copy.deepcopy(plan)
    days = meals_for(changed_plan, offset)
    days[next(i for i, m in enumerate(days) if m["id"] == body.meal_id)] = replacement
    if plan["schema_version"] == 1:
        changed_plan["grocery"] = after_raw
    else:
        changed_plan["grocery"]["main_trip"] = after_raw
    changed_plan["start_date"] = body.start_date.isoformat()
    changed_plan["content_version"] = fingerprint
    current_coverage, next_coverage = coverage(old, lots), coverage(new, lots)
    changes = shopping_diff(before_data, after_data)
    if adjustment:
        affected = {(i["name"].casefold(), i["unit"]) for i in old_diff + new_diff}
        changes = [c for c in changes if (c["name"].casefold(), c["unit"]) in affected]
        requirements = {(i["name"].casefold(), i["unit"]): i for i in net.values()}
        for change in changes:
            requirement = requirements.get((change["name"].casefold(), change["unit"]))
            change["required_quantity"] = requirement["quantity"] if requirement else 0
            if change["kind"] == "kept":
                change["reason"] = "Requirement changed; your purchase or edited quantity is kept."

    return {
        "public": {
            "fingerprint": fingerprint,
            "current": current,
            "replacement": replacement,
            "nutrition_status": replacement.get("nutrition_status", "estimated"),
            "personal_portions": current["portions"],
            "cooking_servings": total_portions(user, current)
            if adjustment
            else servings(user, current),
            "total_before": total_portions(user, current) if adjustment else None,
            "total_after": total_portions(user, replacement) if adjustment else None,
            "audience_before": audience(user, current),
            "audience_after": audience(user, replacement),
            "day_before": totals(meals_for(plan, offset)),
            "day_after": totals(days),
            "goal": plan["daily_energy_kcal"],
            "ingredients_before": current_coverage,
            "ingredients_after": next_coverage,
            "shopping_changes": changes,
            "shopping_scope": "Main trip",
            "shopping_note": "Changed ingredients use recorded stock across the main "
            "trip requirements. Other meals and later fresh refills are retained.",
            "compatibility_note": "Checked against your saved preferences. "
            "Other diners’ allergies not checked."
            if audience(user, replacement) == "shared"
            else "Checked against your saved preferences.",
            "unknown_ingredients": sum(r["coverage"] == "unknown" for r in next_coverage),
        },
        "plan": changed_plan,
        "ledger_before": before_data,
        "ledger_after": after_data,
        "grocery_before": copy.deepcopy(grocery.grouped_items),
        "grocery_after": group(net),
        "raw_before": before_raw,
        "raw_after": after_raw,
        "touched": sorted(touched),
    }


async def preview(
    session: AsyncSession, subject: str, body: PreviewRequest | PortionPreviewRequest
) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    return cast(dict[str, Any], (await proposal(session, user, body))["public"])


def receipt_summary(receipt: MealReplacement) -> dict[str, Any]:
    return {
        "id": str(receipt.request_id),
        "status": receipt.status,
        "kind": receipt.data.get("kind", "replacement"),
        "date": receipt.data["date"],
        "meal_id": receipt.data["meal_id"],
        "name": receipt.data["after_meal"]["name"],
        "slot": receipt.data["after_meal"]["slot"],
    }


async def response(session: AsyncSession, user: User, receipt: MealReplacement) -> dict[str, Any]:
    return {
        "replacement": receipt_summary(receipt),
        "plan": user.starter_plan.preview_payload if user.starter_plan else None,
    }


async def confirm(
    session: AsyncSession, subject: str, body: ConfirmRequest | PortionConfirmRequest
) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    request_hash = digest(body.model_dump(mode="json"))
    existing = await session.get(MealReplacement, (user.id, body.request_id))
    if existing:
        if existing.digest != request_hash:
            fail("This request ID was already used for a different meal change.", "request_reused")
        return await response(session, user, existing)
    request_model = (
        PortionPreviewRequest if isinstance(body, PortionConfirmRequest) else PreviewRequest
    )
    preview_body = request_model.model_validate(
        body.model_dump(exclude={"request_id", "fingerprint"})
    )
    draft = await proposal(session, user, preview_body)
    if draft["public"]["fingerprint"] != body.fingerprint:
        fail(
            "Your plan, preferences, stock or shopping list changed. Review the updated impact.",
            "preview_changed",
        )
    if isinstance(body, PortionConfirmRequest):
        current = draft["public"]["current"]
        if (
            current["portions"] == float(body.personal_portions)
            and draft["public"]["total_before"] == float(body.total_portions)
            and draft["public"]["audience_before"] == body.audience
        ):
            fail("No portion changes to save.", "unchanged", 422)
    ledger = await session.get(CoursesLedger, user.id)
    if ledger is None:
        ledger = CoursesLedger(user_id=user.id, version=0, data=copy.deepcopy(EMPTY))
        session.add(ledger)
    record = user.starter_plan
    assert record is not None
    receipt = MealReplacement(
        user_id=user.id,
        request_id=body.request_id,
        digest=request_hash,
        status="completed",
        data={
            "kind": "portions" if isinstance(body, PortionConfirmRequest) else "replacement",
            "date": body.date.isoformat(),
            "start_date": body.start_date.isoformat(),
            "meal_id": body.meal_id,
            "input_hash": record.preview_payload["input_hash"],
            "plan_id": str(record.id),
            "offset": (body.date - body.start_date).days,
            "before_meal": draft["public"]["current"],
            "after_meal": draft["public"]["replacement"],
            "changes": draft["public"]["shopping_changes"],
            "raw_before": draft["raw_before"],
            "raw_after": draft["raw_after"],
            "grocery_before": draft["grocery_before"],
            "grocery_after": draft["grocery_after"],
            "touched": draft["touched"],
        },
    )
    record.preview_payload = draft["plan"]
    record.content_version = body.fingerprint
    grocery = await session.scalar(
        select(GroceryListRecord).where(GroceryListRecord.user_id == user.id)
    )
    assert grocery is not None
    grocery.grouped_items = draft["grocery_after"]
    grocery.content_version = body.fingerprint
    ledger.data = draft["ledger_after"]
    ledger.version += 1
    session.add(receipt)
    await session.commit()
    return await response(session, user, receipt)


def restore_groups(
    current: list[dict[str, Any]],
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    keys: list[str],
) -> list[dict[str, Any]]:
    rows, old, expected = flatten(current), flatten(before), flatten(after)
    for key in keys:
        if rows.get(key) != expected.get(key):
            fail(
                "Related shopping requirements changed. Review your plan before undoing.",
                "undo_conflict",
            )
        if key in old:
            rows[key] = old[key]
        else:
            rows.pop(key, None)
    return group(rows)


async def undo(session: AsyncSession, subject: str, request_id: uuid.UUID) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    receipt = await session.get(MealReplacement, (user.id, request_id))
    if receipt is None:
        fail("Meal change not found.", status=404)
    if receipt.status == "undone":
        return await response(session, user, receipt)
    data = receipt.data
    record = user.starter_plan
    if (
        record is None
        or str(record.id) != data["plan_id"]
        or (
            record.preview_payload["input_hash"] != data["input_hash"]
            or record.preview_payload.get("start_date") != data["start_date"]
        )
    ):
        fail("Your plan changed since this change. Review your current plan.", "undo_conflict")
    plan = copy.deepcopy(record.preview_payload)
    meals = meals_for(plan, data["offset"])
    index = next((i for i, m in enumerate(meals) if m["id"] == data["meal_id"]), None)
    if index is None or meals[index] != data["after_meal"]:
        fail("This meal changed since the change. Review your current plan.", "undo_conflict")
    ledger = await session.get(CoursesLedger, user.id)
    grocery = await session.scalar(
        select(GroceryListRecord).where(GroceryListRecord.user_id == user.id)
    )
    if ledger is None or grocery is None or ledger.data["plan_version"] != grocery.content_version:
        fail("Your shopping list changed. Refresh it before undoing.", "undo_conflict")
    ledger_data = copy.deepcopy(ledger.data)
    items = {i["id"]: i for i in ledger_data["items"]}
    for change in data["changes"]:
        if change["kind"] == "kept":
            continue
        if items.get(change["id"]) != change["after"]:
            fail(
                "An affected shopping item was changed or purchased. Your change is still saved.",
                "undo_conflict",
            )
        if change["before"]:
            items[change["id"]] = change["before"]
        else:
            items.pop(change["id"], None)
    raw = restore_groups(raw_groups(plan), data["raw_before"], data["raw_after"], data["touched"])
    grocery.grouped_items = restore_groups(
        grocery.grouped_items, data["grocery_before"], data["grocery_after"], data["touched"]
    )
    if plan["schema_version"] == 1:
        plan["grocery"] = raw
    else:
        plan["grocery"]["main_trip"] = raw
    meals[index] = data["before_meal"]
    version = digest([record.content_version, str(request_id), "undo"])
    plan["content_version"] = version
    record.preview_payload, record.content_version = plan, version
    grocery.content_version = version
    ledger_data["items"], ledger_data["plan_version"] = list(items.values()), version
    ledger.data, ledger.version = ledger_data, ledger.version + 1
    receipt.status = "undone"
    await session.commit()
    return await response(session, user, receipt)


async def history(session: AsyncSession, subject: str) -> list[dict[str, Any]]:
    user = await get_user(session, subject)
    rows = await session.scalars(
        select(MealReplacement)
        .where(MealReplacement.user_id == user.id, MealReplacement.status == "completed")
        .order_by(MealReplacement.created_at.desc())
        .limit(50)
    )
    return [
        receipt_summary(r)
        for r in rows
        if user.starter_plan
        and r.data["plan_id"] == str(user.starter_plan.id)
        and r.data["input_hash"] == user.starter_plan.preview_payload["input_hash"]
    ]


async def status(session: AsyncSession, subject: str, request_id: uuid.UUID) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    receipt = await session.get(MealReplacement, (user.id, request_id))
    if receipt is None:
        return {
            "replacement": None,
            "plan": user.starter_plan.preview_payload if user.starter_plan else None,
        }
    return await response(session, user, receipt)
