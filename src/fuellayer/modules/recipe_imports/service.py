import copy
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.modules.foods.models import CatalogueFood
from fuellayer.modules.onboarding.models import User
from fuellayer.modules.onboarding.schemas import AllergenCode, DietaryPattern
from fuellayer.modules.preferences.constraints import allergy_matches, excluded_matches
from fuellayer.modules.recipe_imports.fetch import ImportFailure, normalize_url
from fuellayer.modules.recipe_imports.models import (
    ImportCommand,
    ImportedRecipe,
    ImportJob,
    ImportSource,
    RecipeIngredientRow,
    RecipeNutritionRow,
    RecipeStepRow,
)
from fuellayer.modules.recipe_imports.schemas import (
    Correction,
    ImportInput,
    RecipeData,
    RecipeFields,
    Save,
)
from fuellayer.modules.recipes.service import get_user

NUTRIENTS = ("calories_kcal", "protein_g", "carbohydrates_g", "fat_g")


def now() -> datetime:
    return datetime.now(UTC)


def fail(code: str, message: str, status: int = 409) -> NoReturn:
    raise HTTPException(status, detail={"code": code, "message": message, "retryable": False})


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def recipe_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value.removeprefix("import:"))
    except ValueError:
        fail("recipe_missing", "Recipe not found.", 404)


async def owned(session: AsyncSession, user: User, value: str) -> ImportedRecipe:
    recipe = await session.scalar(
        select(ImportedRecipe).where(
            ImportedRecipe.id == recipe_uuid(value), ImportedRecipe.user_id == user.id
        )
    )
    if recipe is None:
        fail("recipe_missing", "Recipe not found.", 404)
    return recipe


async def job_for(session: AsyncSession, user: User, job_id: uuid.UUID) -> ImportJob:
    job = await session.scalar(
        select(ImportJob).where(ImportJob.id == job_id, ImportJob.user_id == user.id)
    )
    if job is None:
        fail("import_missing", "Import not found.", 404)
    return job


def version(actual: int, expected: int) -> None:
    if actual != expected:
        fail("version_conflict", "This recipe changed. Reload and review your edits before saving.")


async def replay(
    session: AsyncSession, user: User, request_id: uuid.UUID, operation: str, payload: Any
) -> dict[str, Any] | None:
    previous = await session.get(ImportCommand, (user.id, request_id))
    if previous:
        if previous.digest != digest([operation, payload]):
            fail("request_mismatch", "This request ID was already used for a different action.")
        if previous.data.get("expired"):
            fail("request_expired", "This request expired. Reload the recipe before continuing.")
        return copy.deepcopy(previous.data)
    return None


async def commit_command(
    session: AsyncSession,
    user: User,
    request_id: uuid.UUID,
    operation: str,
    payload: Any,
    result: dict[str, Any],
) -> dict[str, Any]:
    session.add(
        ImportCommand(
            user_id=user.id, request_id=request_id, digest=digest([operation, payload]), data=result
        )
    )
    await session.commit()
    return result


def job_view(job: ImportJob, duplicate: bool = False) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "state": job.state,
        "stage": job.state,
        "version": job.version,
        "recipe_id": f"import:{job.recipe_id}" if job.recipe_id else None,
        "duplicate": duplicate,
        "poll_after_ms": 2000
        if job.state not in ("failed", "cancelled", "ready_for_review", "awaiting_selection")
        else 0,
        "error": job.error,
        "source_url": job.input.get("url") or job.input.get("source_url"),
        "candidates": job.checkpoint.get("candidates", []),
        "created_at": job.created_at.isoformat(),
    }


async def start(
    session: AsyncSession, subject: str, request_id: uuid.UUID, payload: ImportInput
) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    body = payload.model_dump()
    previous = await replay(session, user, request_id, "start", body)
    if previous is not None:
        return previous
    if payload.kind == "url":
        try:
            body["url"] = normalize_url(payload.url or "")
        except ImportFailure as exc:
            fail(exc.code, exc.message, 422)
    key = (
        digest(
            [
                payload.kind,
                body.get("url") if payload.kind == "url" else (payload.text or "").strip(),
            ]
        )
        if payload.kind != "manual"
        else digest(str(request_id))
    )
    source = await session.get(ImportSource, (user.id, key))
    if source:
        old = await job_for(session, user, source.job_id)
        return await commit_command(
            session, user, request_id, "start", payload.model_dump(), job_view(old, True)
        )
    count = await session.scalar(
        select(func.count())
        .select_from(ImportJob)
        .where(ImportJob.user_id == user.id, ImportJob.created_at > now() - timedelta(days=1))
    )
    if (count or 0) >= 30:
        fail("rate_limited", "Your daily recipe import limit is reached. Try again tomorrow.", 429)
    active = await session.scalar(
        select(func.count())
        .select_from(ImportJob)
        .where(
            ImportJob.user_id == user.id,
            ImportJob.state.in_(
                ["queued", "fetching", "parsing", "structuring", "validating", "retry_wait"]
            ),
        )
    )
    if (active or 0) >= 2 and payload.kind != "manual":
        fail("rate_limited", "Two imports are already processing. Let one finish first.", 429)
    job = ImportJob(user_id=user.id, input=body, state="queued")
    session.add(job)
    await session.flush()
    session.add(ImportSource(user_id=user.id, key=key, job_id=job.id))
    if payload.kind == "manual":
        recipe = await create_recipe(
            session,
            user.id,
            RecipeData(fields=RecipeFields(source_url=payload.source_url)),
            "manual",
        )
        job.recipe_id, job.state = recipe.id, "ready_for_review"
    return await commit_command(
        session, user, request_id, "start", payload.model_dump(), job_view(job)
    )


async def write_rows(session: AsyncSession, recipe: ImportedRecipe, data: RecipeData) -> None:
    groups: list[tuple[Any, list[Any]]] = [
        (RecipeIngredientRow, data.ingredients),
        (RecipeStepRow, data.steps),
    ]
    for table, values in groups:
        existing = {
            str(row.id): row
            for row in await session.scalars(
                select(table).where(table.recipe_id == recipe.id, table.user_id == recipe.user_id)
            )
        }
        seen: set[str] = set()
        rows = []
        for position, value in enumerate(values):
            if value.id and (value.id not in existing or value.id in seen):
                fail("invalid_row", "A recipe row is no longer available. Reload your recipe.", 422)
            if value.id:
                seen.add(value.id)
            fields = value.model_dump(exclude={"id"})
            prior = existing.get(value.id or "")
            # Clients cannot manufacture source provenance after editing a row.
            if prior and {
                k: v for k, v in prior.data.items() if k not in ("origin", "evidence")
            } != {k: v for k, v in fields.items() if k not in ("origin", "evidence")}:
                fields.update(origin="user", evidence=prior.data.get("evidence"))
            elif prior:
                fields.update(
                    origin=prior.data.get("origin", "user"), evidence=prior.data.get("evidence")
                )
            rows.append(
                table(
                    id=uuid.UUID(value.id) if value.id else uuid.uuid4(),
                    recipe_id=recipe.id,
                    user_id=recipe.user_id,
                    position=position,
                    data=fields,
                )
            )
        await session.execute(
            delete(table).where(table.recipe_id == recipe.id, table.user_id == recipe.user_id)
        )
        await session.flush()
        session.add_all(rows)
    for kind, value in (("source", data.source_nutrition), ("manual", data.manual_nutrition)):
        row = await session.get(RecipeNutritionRow, (recipe.id, kind))
        if row:
            row.data = value.model_dump()
        else:
            session.add(
                RecipeNutritionRow(
                    recipe_id=recipe.id, user_id=recipe.user_id, kind=kind, data=value.model_dump()
                )
            )
    recipe.fields, recipe.nutrition_choice = data.fields.model_dump(), data.nutrition_choice
    recipe.updated_at = now()
    await session.flush()


async def create_recipe(
    session: AsyncSession, user_id: uuid.UUID, data: RecipeData, origin: str
) -> ImportedRecipe:
    recipe = ImportedRecipe(
        user_id=user_id,
        provenance={
            key: {"origin": origin, "original": value}
            for key, value in data.fields.model_dump().items()
        },
    )
    session.add(recipe)
    await session.flush()
    await write_rows(session, recipe, data)
    return recipe


async def recipe_data(session: AsyncSession, recipe: ImportedRecipe) -> RecipeData:
    ingredients = [
        {**row.data, "id": str(row.id)}
        for row in await session.scalars(
            select(RecipeIngredientRow)
            .where(
                RecipeIngredientRow.recipe_id == recipe.id,
                RecipeIngredientRow.user_id == recipe.user_id,
            )
            .order_by(RecipeIngredientRow.position)
        )
    ]
    steps = [
        {**row.data, "id": str(row.id)}
        for row in await session.scalars(
            select(RecipeStepRow)
            .where(RecipeStepRow.recipe_id == recipe.id, RecipeStepRow.user_id == recipe.user_id)
            .order_by(RecipeStepRow.position)
        )
    ]
    nutrition = {
        row.kind: row.data
        for row in await session.scalars(
            select(RecipeNutritionRow).where(
                RecipeNutritionRow.recipe_id == recipe.id,
                RecipeNutritionRow.user_id == recipe.user_id,
            )
        )
    }
    return RecipeData.model_validate(
        {
            "fields": recipe.fields,
            "ingredients": ingredients,
            "steps": steps,
            "source_nutrition": nutrition.get("source", {}),
            "manual_nutrition": nutrition.get("manual", {}),
            "nutrition_choice": recipe.nutrition_choice,
        }
    )


async def calculated(session: AsyncSession, data: RecipeData) -> dict[str, Any]:
    values: dict[str, float | None] = {key: 0 for key in NUTRIENTS}
    missing = []
    inputs = []
    for item in data.ingredients:
        food = await session.get(CatalogueFood, item.food_id) if item.food_id else None
        grams = item.grams
        if grams is None and item.unit in ("g", "kg") and item.quantity is not None:
            grams = item.quantity * (1000 if item.unit == "kg" else 1)
        if food is None or grams is None or item.quantity is None:
            missing.append(item.name or "Unnamed ingredient")
            values = {key: None for key in NUTRIENTS}
            continue
        inputs.append({"food_id": food.id, "food_version": food.source_version, "grams": grams})
        for key in NUTRIENTS:
            nutrient = food.nutrients.get(key)
            current = values[key]
            values[key] = (
                round(current + grams * nutrient / 100, 4)
                if current is not None and nutrient is not None
                else None
            )
    if not data.ingredients:
        values = {key: None for key in NUTRIENTS}
    return {
        "values": values,
        "basis": "whole_recipe",
        "valid": True,
        "missing_ingredients": missing,
        "inputs": inputs,
        "calculation_version": "ciqual-grams-v1",
    }


def review_issues(data: RecipeData) -> list[dict[str, str]]:
    issues = []

    def add(key: str, message: str) -> None:
        issues.append({"id": key, "message": message})

    if not data.fields.title or not data.fields.title.strip():
        add("title", "Add a recipe title.")
    if data.fields.servings is None:
        add("servings", "Servings are missing. Add the source recipe yield.")
    if not data.ingredients:
        add("ingredients", "Add the ingredients.")
    for i, item in enumerate(data.ingredients):
        if not item.name.strip():
            add(f"ingredient.{i}.name", "An ingredient needs a name.")
        if (
            item.quantity is None or item.quantity <= 0 or not item.unit or not item.unit.strip()
        ) and item.amount_kind != "as_needed":
            add(
                f"ingredient.{i}.quantity",
                f"{item.name or 'Ingredient'}: quantity or unit missing.",
            )
    if not data.steps or any(not step.text.strip() for step in data.steps):
        add("steps", "No preparation steps found. Paste or enter them.")
    return issues


async def detail(session: AsyncSession, user: User, recipe: ImportedRecipe) -> dict[str, Any]:
    data = await recipe_data(session, recipe)
    estimate = await calculated(session, data)
    sets = {
        "source": data.source_nutrition.model_dump(),
        "manual": data.manual_nutrition.model_dump(),
        "calculated": estimate,
    }
    chosen = sets[data.nutrition_choice]
    basis = (
        1
        if chosen["basis"] == "per_serving"
        else data.fields.servings
        if chosen["basis"] == "whole_recipe"
        else None
    )
    values = chosen["values"] if chosen["valid"] and basis else {key: None for key in NUTRIENTS}
    normalized = {
        key: value / basis if value is not None and basis else None for key, value in values.items()
    }
    status = "estimated" if data.nutrition_choice == "calculated" else "known"
    fields = data.fields
    preferences = user.food_preferences
    conflict = preferences is not None and bool(
        allergy_matches(preferences.allergens, fields.allergens)
        or excluded_matches(
            preferences.excluded_ingredients or [], (i.name for i in data.ingredients)
        )
    )
    compatibility = (
        "conflict"
        if conflict
        else "matches"
        if fields.compatibility_reviewed
        and preferences
        and preferences.dietary_pattern in fields.dietary_patterns
        else "unknown"
    )
    issues = review_issues(data)
    base_reasons = ["Finish reviewing this draft."] if recipe.status != "reviewed" else []
    log_reasons = [
        *base_reasons,
        *(
            []
            if normalized["calories_kcal"] is not None
            else ["Calories with an explicit portion basis are needed."]
        ),
    ]
    plan_reasons = [*base_reasons]
    if fields.servings is None:
        plan_reasons.append("Recipe servings are missing.")
    if not data.ingredients or any(
        i.quantity is None or i.quantity <= 0 or not i.unit or i.amount_kind != "numeric"
        for i in data.ingredients
    ):
        plan_reasons.append("Every ingredient needs a scalable quantity and unit.")
    if len({(i.name, i.unit) for i in data.ingredients}) != len(data.ingredients):
        plan_reasons.append(
            "Combine repeated ingredients with the same name and unit before planning."
        )
    if any(normalized[key] is None for key in NUTRIENTS):
        plan_reasons.append("Calories and all three macros need an explicit portion basis.")
    if compatibility != "matches":
        plan_reasons.append(
            "Review food preferences and allergens; known conflicts cannot be used."
        )
    from fuellayer.modules.kitchen.models import KitchenItem

    ingredient_keys = set(
        await session.scalars(
            select(KitchenItem.ingredient_key).where(
                KitchenItem.user_id == user.id,
                KitchenItem.status == "active",
                KitchenItem.ingredient_key.is_not(None),
                KitchenItem.quantity.is_(None) | (KitchenItem.quantity > 0),
            )
        )
    )
    return {
        **data.model_dump(),
        "id": f"import:{recipe.id}",
        "name": fields.title or "Untitled import",
        "version": recipe.version,
        "status": recipe.status,
        "source": "user_import",
        "source_attribution": {
            "url": fields.source_url,
            "author": fields.author,
            "publisher": fields.publisher,
        },
        "ingredient_names": [i.name for i in data.ingredients],
        "prep_minutes": fields.prep_minutes,
        "description": fields.description or "",
        "slot": "",
        "ingredients_reference_servings": fields.servings,
        "nutrition": {
            "calories_kcal": normalized["calories_kcal"],
            "reference_servings": 1 if basis else None,
            "status": status if normalized["calories_kcal"] is not None else "missing",
        },
        "macros": {
            key: {
                "value": normalized[key],
                "status": status if normalized[key] is not None else "missing",
            }
            for key in NUTRIENTS[1:]
        },
        "nutrition_sets": sets,
        "nutrition_origin": data.nutrition_choice,
        "steps_status": "available" if data.steps else "missing",
        "dietary_patterns": fields.dietary_patterns,
        "allergens": fields.allergens,
        "compatibility": compatibility,
        "saved": recipe.library_saved_at is not None,
        "in_plan": False,
        "at_home_ingredient_names": [i.name for i in data.ingredients if i.name in ingredient_keys],
        "issues": issues,
        "provenance": recipe.provenance,
        "capabilities": {
            "log": {"allowed": not log_reasons, "reasons": log_reasons},
            "replace": {"allowed": not plan_reasons, "reasons": plan_reasons},
            "scale": {
                "allowed": fields.servings is not None,
                "reasons": [] if fields.servings else ["Servings are missing."],
            },
        },
    }


async def correct(
    session: AsyncSession, subject: str, value: str, request_id: uuid.UUID, payload: Correction
) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    body = payload.model_dump()
    operation = "correct:" + value
    previous = await replay(session, user, request_id, operation, body)
    if previous is not None:
        return previous
    recipe = await owned(session, user, value)
    version(recipe.version, payload.expected_version)
    data = RecipeData.model_validate(
        payload.model_dump(exclude={"expected_version", "confirm_compatibility"})
    )
    if not set(data.fields.allergens).issubset({v.value for v in AllergenCode}) or not set(
        data.fields.dietary_patterns
    ).issubset({v.value for v in DietaryPattern}):
        fail("invalid_preferences", "Choose supported dietary preferences and allergens.", 422)
    old = await recipe_data(session, recipe)
    # Evidence and validity belong to the server; only interpretation of the basis is editable.
    data.source_nutrition.evidence = old.source_nutrition.evidence
    if data.source_nutrition.values != old.source_nutrition.values:
        fail(
            "source_nutrition_read_only",
            "Enter corrected nutrition under Manual; original source values are preserved.",
            422,
        )
    composition_changed = [
        i.model_dump(exclude={"id", "origin", "evidence"}) for i in data.ingredients
    ] != [i.model_dump(exclude={"id", "origin", "evidence"}) for i in old.ingredients]
    if composition_changed or data.fields.servings != old.fields.servings:
        data.source_nutrition.valid = False
        if data.manual_nutrition == old.manual_nutrition:
            data.manual_nutrition.valid = False
        if composition_changed:
            data.fields.compatibility_reviewed = payload.confirm_compatibility
    elif not old.source_nutrition.valid:
        data.source_nutrition.valid = False
    provenance = copy.deepcopy(recipe.provenance)
    for key, val in data.fields.model_dump().items():
        if old.fields.model_dump()[key] != val:
            provenance[key] = {
                "origin": "user",
                "original": provenance.get(key, {}).get("original"),
                "updated_at": now().isoformat(),
            }
    recipe.provenance = provenance
    recipe.version += 1
    recipe.status, recipe.reviewed_at = "draft", None
    for item in data.ingredients:
        if item.id is None:
            item.origin, item.evidence = "user", None
    for step in data.steps:
        if step.id is None:
            step.origin, step.evidence = "user", None
    await write_rows(session, recipe, data)
    return await commit_command(
        session, user, request_id, operation, body, await detail(session, user, recipe)
    )


async def save(
    session: AsyncSession, subject: str, value: str, request_id: uuid.UUID, payload: Save
) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    body, operation = payload.model_dump(), "save:" + value
    previous = await replay(session, user, request_id, operation, body)
    if previous is not None:
        return previous
    recipe = await owned(session, user, value)
    version(recipe.version, payload.expected_version)
    data = await recipe_data(session, recipe)
    if payload.status == "reviewed" and review_issues(data):
        fail(
            "review_incomplete",
            "Resolve missing title, servings, ingredients and preparation, or save a draft.",
            422,
        )
    recipe.status, recipe.version = payload.status, recipe.version + 1
    recipe.library_saved_at, recipe.updated_at = now(), now()
    recipe.reviewed_at = now() if payload.status == "reviewed" else None
    return await commit_command(
        session, user, request_id, operation, body, await detail(session, user, recipe)
    )


async def remove(
    session: AsyncSession, subject: str, value: str, request_id: uuid.UUID
) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    previous = await replay(session, user, request_id, "delete:" + value, {})
    if previous is not None:
        return previous
    recipe = await session.scalar(
        select(ImportedRecipe).where(
            ImportedRecipe.id == recipe_uuid(value), ImportedRecipe.user_id == user.id
        )
    )
    if recipe:
        for job in await session.scalars(
            select(ImportJob).where(ImportJob.user_id == user.id, ImportJob.recipe_id == recipe.id)
        ):
            job.state, job.lease, job.lease_until, job.checkpoint, job.input = (
                "cancelled",
                None,
                None,
                {},
                {},
            )
            job.recipe_id = None
            job.version += 1
            await session.execute(
                delete(ImportSource).where(
                    ImportSource.user_id == user.id, ImportSource.job_id == job.id
                )
            )
        # Erase sensitive replay snapshots; keep digest tombstones to prohibit resurrection.
        for command in await session.scalars(
            select(ImportCommand).where(ImportCommand.user_id == user.id)
        ):
            if value in json.dumps(command.data):
                command.data = {"expired": True}
        await session.delete(recipe)
    return await commit_command(session, user, request_id, "delete:" + value, {}, {"deleted": True})
