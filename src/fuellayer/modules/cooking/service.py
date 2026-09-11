import copy
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, NoReturn

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.modules.cooking.domain import convert, digest, timer_candidates
from fuellayer.modules.cooking.models import CookingCommand, CookingConsumption, CookingSession
from fuellayer.modules.cooking.schemas import Command, IngredientUse, Start
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.kitchen.schemas import KitchenItemView
from fuellayer.modules.recipe_imports import service as imports
from fuellayer.modules.recipes.service import build_detail, get_user

UNFINISHED = ("in_progress", "awaiting_confirmation")


def fail(code: str, message: str, status: int = 409) -> NoReturn:
    raise HTTPException(status, detail={"code": code, "message": message})


async def owned(db: AsyncSession, owner: uuid.UUID, sid: uuid.UUID) -> CookingSession:
    row = await db.scalar(
        select(CookingSession).where(CookingSession.id == sid, CookingSession.user_id == owner)
    )
    if row is None:
        fail("cooking_missing", "Cooking session not found.", 404)
    return row


async def view(db: AsyncSession, row: CookingSession) -> dict[str, Any]:
    consumption = await db.get(CookingConsumption, row.id)
    return {
        "schema_version": 1,
        "id": str(row.id),
        "status": row.status,
        "version": row.version,
        "snapshot": row.snapshot,
        **row.state,
        "server_now": time.time(),
        "stock": None
        if consumption is None
        else {"status": consumption.status, "lots": consumption.lots, "uses": consumption.uses},
    }


async def replay(
    db: AsyncSession, owner: uuid.UUID, request: uuid.UUID, hash_value: str
) -> dict[str, Any] | None:
    receipt = await db.get(CookingCommand, (owner, request))
    if receipt:
        if receipt.digest != hash_value:
            fail("request_conflict", "This request was already used for another action.")
        return await view(db, await owned(db, owner, receipt.session_id))
    return None


async def commit(
    db: AsyncSession, row: CookingSession, request: uuid.UUID, hash_value: str
) -> dict[str, Any]:
    row.updated_at = datetime.now(UTC)
    await db.flush()
    result = await view(db, row)
    db.add(
        CookingCommand(
            user_id=row.user_id,
            request_id=request,
            session_id=row.id,
            digest=hash_value,
            result=result,
        )
    )
    await db.commit()
    return result


async def start(db: AsyncSession, subject: str, payload: Start) -> dict[str, Any]:
    user = await get_user(db, subject, lock=True)
    hash_value = digest(["start", payload.model_dump(mode="json")])
    previous = await replay(db, user.id, payload.request_id, hash_value)
    if previous:
        return previous
    active = await db.scalar(
        select(CookingSession).where(
            CookingSession.user_id == user.id, CookingSession.status.in_(UNFINISHED)
        )
    )
    if active:
        fail(
            "active_session_exists",
            "An unfinished cooking session exists. Resume or abandon it first.",
        )
    if payload.recipe_id.startswith("import:"):
        recipe = await imports.owned(db, user, payload.recipe_id)
        if payload.recipe_version != recipe.version:
            fail("source_changed", "This recipe changed. Reopen it before cooking.")
        detail = await imports.detail(db, user, recipe)
    else:
        detail = build_detail(payload.recipe_id).model_dump(mode="json")
    steps = detail.get("steps", [])
    if not steps or any(not step.get("text", "").strip() for step in steps):
        fail("missing_steps", "Add the missing preparation instructions before cooking.", 422)
    if detail.get("status") == "draft" and not payload.acknowledge_draft:
        fail("draft_acknowledgement", "Acknowledge that this recipe is still a draft.", 422)
    basis = detail.get("ingredients_reference_servings") or payload.source_yield
    if not basis:
        fail("missing_yield", "Enter the original recipe yield to scale ingredients.", 422)
    detail = copy.deepcopy(detail)
    detail["steps"] = [{**s, "id": str(s.get("id") or f"step-{i}")} for i, s in enumerate(steps)]
    detail["ingredients"] = [
        {**item, "id": str(item.get("id") or f"ingredient-{i}")}
        for i, item in enumerate(detail["ingredients"])
    ]
    scaled = [
        {
            **i,
            "quantity": float(
                Decimal(str(i["quantity"])) * Decimal(str(payload.portions)) / Decimal(str(basis))
            )
            if i.get("quantity") is not None and i.get("amount_kind", "numeric") == "numeric"
            else None,
        }
        for i in detail["ingredients"]
    ]
    row = CookingSession(
        user_id=user.id,
        snapshot={
            "recipe": detail,
            "portions": payload.portions,
            "source_yield": basis,
            "basis_origin": "source" if detail.get("ingredients_reference_servings") else "user",
            "ingredients": scaled,
            "timer_candidates": timer_candidates(detail["steps"]),
        },
        state={
            "current_step": detail["steps"][0]["id"],
            "completed_steps": [],
            "timers": [],
            "uses": [],
        },
    )
    db.add(row)
    await db.flush()
    return await commit(db, row, payload.request_id, hash_value)


async def preview(
    db: AsyncSession, row: CookingSession, uses: list[IngredientUse]
) -> dict[str, Any]:
    ids = {i["id"] for i in row.snapshot["ingredients"]}
    if len(uses) != len(ids) or {u.ingredient_id for u in uses} != ids:
        fail("unresolved_ingredients", "Resolve or skip every ingredient before confirming.", 422)
    aggregates: dict[uuid.UUID, Decimal] = {}
    lots: dict[uuid.UUID, KitchenItem] = {}
    for use in uses:
        if use.skip:
            if use.allocations:
                fail("invalid_skip", "A skipped ingredient cannot deduct stock.", 422)
            continue
        if not use.allocations:
            fail("missing_lot", "Choose a pantry lot or skip this ingredient.", 422)
        for allocation in use.allocations:
            lot = await db.scalar(
                select(KitchenItem).where(
                    KitchenItem.id == allocation.lot_id, KitchenItem.user_id == row.user_id
                )
            )
            if lot is None:
                fail("lot_missing", "Pantry lot not found.", 404)
            if lot.version != allocation.expected_version or lot.status != "active":
                fail("stock_changed", f"{lot.name} changed. Reload stock and review usage.")
            if lot.quantity is None or lot.unit is None:
                fail(
                    "unknown_stock",
                    f"Set the quantity and unit for {lot.name} in Kitchen, or skip it.",
                    422,
                )
            amount = convert(allocation.quantity, allocation.unit, lot.unit)
            if amount is None:
                fail("incompatible_unit", f"Enter actual usage in {lot.unit} for {lot.name}.", 422)
            lots[lot.id] = lot
            aggregates[lot.id] = aggregates.get(lot.id, Decimal(0)) + amount
    deductions = []
    for lot_id in sorted(lots):
        lot = lots[lot_id]
        before = Decimal(str(lot.quantity))
        after = before - aggregates[lot_id]
        if after < 0:
            fail(
                "insufficient_stock",
                f"Not enough {lot.name}. Reduce usage, choose another lot or skip.",
            )
        deductions.append(
            {
                "id": str(lot.id),
                "name": lot.name,
                "unit": lot.unit,
                "before": float(before),
                "quantity": float(aggregates[lot_id]),
                "after": float(after),
                "before_status": lot.status,
                "before_version": lot.version,
                "after_version": lot.version + 1,
            }
        )
    body = {
        "session_id": str(row.id),
        "version": row.version,
        "uses": [u.model_dump(mode="json") for u in uses],
        "lots": deductions,
    }
    return {**body, "fingerprint": digest(body)}


def active_timers(row: CookingSession) -> bool:
    return any(t["status"] in ("running", "paused") for t in row.state["timers"])


async def execute(
    db: AsyncSession, subject: str, sid: uuid.UUID, command: Command
) -> dict[str, Any]:
    user = await get_user(db, subject, lock=True)
    hash_value = digest([str(sid), command.model_dump(mode="json")])
    previous = await replay(db, user.id, command.request_id, hash_value)
    if previous:
        return previous
    row = await owned(db, user.id, sid)
    if command.expected_version != row.version:
        fail(
            "cooking_changed", "This session changed. Reload its saved progress before continuing."
        )
    if row.status not in UNFINISHED and command.action != "undo":
        fail("cooking_finished", "This cooking session has already ended.")
    state = copy.deepcopy(row.state)
    step_ids = {s["id"] for s in row.snapshot["recipe"]["steps"]}
    if command.action in ("progress", "resume"):
        if command.current_step is not None:
            if command.current_step not in step_ids:
                fail("invalid_step", "Step not found.", 422)
            state["current_step"] = command.current_step
            row.status = "in_progress"
        if command.completed_steps is not None:
            if not set(command.completed_steps) <= step_ids:
                fail("invalid_step", "Step not found.", 422)
            state["completed_steps"] = list(dict.fromkeys(command.completed_steps))
        if command.uses is not None:
            if not {u.ingredient_id for u in command.uses} <= {
                i["id"] for i in row.snapshot["ingredients"]
            }:
                fail("invalid_ingredient", "Ingredient not found.", 422)
            state["uses"] = [u.model_dump(mode="json") for u in command.uses]
    elif command.action == "review":
        if set(state["completed_steps"]) != step_ids and not command.early_finish:
            fail("unfinished_steps", "Confirm that you want to finish with incomplete steps.")
        row.status = "awaiting_confirmation"
    elif command.action.startswith("timer_"):
        now = time.time()
        tid = str(command.timer_id or "")
        timer = next((t for t in state["timers"] if t["id"] == tid), None)
        if command.action == "timer_start":
            if not command.timer_id or not command.duration_seconds or timer:
                fail("invalid_timer", "Choose a new timer and a duration.", 422)
            if (
                sum(
                    t["status"] in ("running", "paused")
                    and (t["status"] == "paused" or t["deadline"] > now)
                    for t in state["timers"]
                )
                >= 8
            ):
                fail("timer_limit", "Finish a timer before adding another.", 422)
            if command.candidate_id:
                candidate = next(
                    (
                        c
                        for c in row.snapshot["timer_candidates"]
                        if c["id"] == command.candidate_id
                    ),
                    None,
                )
                if (
                    not candidate
                    or not candidate["minimum"] <= command.duration_seconds <= candidate["maximum"]
                ):
                    fail("invalid_duration", "Choose a duration from the source instruction.", 422)
            started = command.started_at if command.started_at is not None else now
            if not now - 7 * 86400 <= started <= now + 60:
                fail("invalid_timer_time", "Timer start time needs to be reconciled.", 422)
            timer = {
                "id": tid,
                "label": command.label,
                "duration": command.duration_seconds,
                "deadline": started + command.duration_seconds,
                "remaining": command.duration_seconds,
                "status": "running",
                "revision": 1,
            }
            state["timers"].append(timer)
        else:
            if not timer:
                fail("timer_missing", "Timer not found.", 404)
            if command.action == "timer_pause":
                if timer["status"] != "running":
                    fail("timer_changed", "This timer is not running.")
                timer["remaining"] = max(0, timer["deadline"] - now)
                timer["status"] = "paused" if timer["remaining"] else "elapsed"
            elif command.action == "timer_resume":
                if timer["status"] != "paused":
                    fail("timer_changed", "This timer is not paused.")
                timer.update(status="running", deadline=now + timer["remaining"])
            elif command.action == "timer_restart":
                timer.update(
                    status="running", deadline=now + timer["duration"], remaining=timer["duration"]
                )
            else:
                timer["status"] = "cancelled"
            timer["revision"] += 1
    elif command.action in ("complete", "abandon"):
        if active_timers(row) and not command.stop_timers:
            fail("running_timers", "Confirm stopping the session’s timers before finishing.")
        for timer in state["timers"]:
            if timer["status"] in ("running", "paused"):
                timer["status"] = "cancelled"
                timer["revision"] += 1
        if command.action == "complete":
            if row.status != "awaiting_confirmation" or command.stock_action is None:
                fail("review_required", "Review ingredient usage before completing cooking.")
            deductions = []
            uses = command.uses or []
            if command.stock_action == "confirm":
                proposal = await preview(db, row, uses)
                if command.fingerprint != proposal["fingerprint"]:
                    fail("preview_changed", "Review the current stock preview before confirming.")
                deductions = proposal["lots"]
                for deduction in deductions:
                    lot = await db.get(KitchenItem, uuid.UUID(deduction["id"]))
                    assert lot is not None
                    lot.quantity, lot.version = deduction["after"], deduction["after_version"]
                    lot.status = "finished" if lot.quantity == 0 else "active"
            db.add(
                CookingConsumption(
                    session_id=row.id,
                    user_id=user.id,
                    status="applied" if command.stock_action == "confirm" else "skipped",
                    lots=deductions,
                    uses=[u.model_dump(mode="json") for u in uses],
                )
            )
            row.status = "completed"
        else:
            row.status = "abandoned"
    elif command.action == "undo":
        consumption = await db.get(CookingConsumption, row.id)
        if row.status != "completed" or not consumption or consumption.status != "applied":
            fail("undo_unavailable", "No pantry deduction is available to undo.")
        for entry in consumption.lots:
            lot = await db.get(KitchenItem, uuid.UUID(entry["id"]))
            if lot is None or lot.user_id != user.id or lot.version != entry["after_version"]:
                fail("undo_conflict", "Pantry stock changed after cooking. Correct it in Kitchen.")
        for entry in consumption.lots:
            lot = await db.get(KitchenItem, uuid.UUID(entry["id"]))
            assert lot is not None
            lot.quantity, lot.status = entry["before"], entry["before_status"]
            lot.version += 1
        consumption.status, consumption.undone_at = "undone", datetime.now(UTC)
    row.state = state
    row.version += 1
    return await commit(db, row, command.request_id, hash_value)


async def pantry(db: AsyncSession, owner: uuid.UUID) -> list[dict[str, Any]]:
    lots = await db.scalars(
        select(KitchenItem).where(KitchenItem.user_id == owner, KitchenItem.status == "active")
    )
    return [KitchenItemView.model_validate(lot).model_dump(mode="json") for lot in lots]
