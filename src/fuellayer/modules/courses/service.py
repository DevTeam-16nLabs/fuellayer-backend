"""All stock and ledger writes share the existing per-account lock and transaction."""

import copy
import hashlib
import unicodedata
import uuid
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, NoReturn

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.config import settings
from fuellayer.modules.courses.models import CoursesLedger, CoursesRequest
from fuellayer.modules.courses.schemas import Command, FoodDetails
from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.kitchen.schemas import KitchenItemView
from fuellayer.modules.onboarding.models import GroceryListRecord
from fuellayer.modules.recipes.service import get_user


def fail(message: str, status: int = 409) -> NoReturn:
    raise HTTPException(status, detail={"code": "courses_conflict", "message": message})


def normalized(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(c)
    ).strip()


def uid() -> str:
    return str(uuid.uuid4())


def now() -> str:
    return datetime.now(UTC).isoformat()


async def ledger_for(session: AsyncSession, user_id: uuid.UUID) -> CoursesLedger:
    ledger = await session.get(CoursesLedger, user_id)
    if ledger is None:
        ledger = CoursesLedger(
            user_id=user_id,
            version=1,
            data={"items": [], "receipts": [], "transfers": [], "plan_version": None},
        )
        session.add(ledger)
        await session.flush()
    return ledger


def reconcile(data: dict[str, Any], groups: list[dict[str, Any]], version: str) -> None:
    """Only replace untouched unchecked suggestions. Purchases and overrides are durable."""
    if data["plan_version"] == version:
        return
    present = set()
    occurrences: dict[str, int] = {}
    for section in groups:
        for entry in section.get("items", []):
            base_key = f"{section['aisle']}:{entry['name']}:{entry.get('unit')}"
            occurrence = occurrences.get(base_key, 0)
            occurrences[base_key] = occurrence + 1
            key = hashlib.sha256(f"{base_key}:{occurrence}".encode()).hexdigest()
            present.add(key)
            old = next((item for item in data["items"] if item.get("source_key") == key), None)
            if old and (old["status"] != "to_buy" or old.get("overridden")):
                continue
            values = {
                "name": entry["name"],
                "quantity": entry.get("quantity"),
                "unit": entry.get("unit"),
                "aisle": section["aisle"],
                "category": "Non classé",
                "location": "unassigned",
            }
            if old:
                old.update(values)
            else:
                data["items"].append(
                    {
                        "id": uid(),
                        **values,
                        "status": "to_buy",
                        "source_key": key,
                        "overridden": False,
                        "lot_id": None,
                    }
                )
    data["items"] = [
        item
        for item in data["items"]
        if not item.get("source_key")
        or item["source_key"] in present
        or item["status"] != "to_buy"
        or item.get("overridden")
    ]
    data["plan_version"] = version


async def view(session: AsyncSession, ledger: CoursesLedger) -> dict[str, Any]:
    lots = await session.scalars(select(KitchenItem).where(KitchenItem.user_id == ledger.user_id))
    return {
        "version": ledger.version,
        **ledger.data,
        "scan_available": bool(settings.receipt_api_key),
        "scan_provider": settings.receipt_provider,
        "lots": [KitchenItemView.model_validate(lot).model_dump(mode="json") for lot in lots],
    }


async def read(session: AsyncSession, subject: str) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    ledger = await ledger_for(session, user.id)
    plan = await session.scalar(
        select(GroceryListRecord).where(GroceryListRecord.user_id == user.id)
    )
    if plan and ledger.data["plan_version"] != plan.content_version:
        data = copy.deepcopy(ledger.data)
        reconcile(data, plan.grouped_items, plan.content_version)
        ledger.data = data
        ledger.version += 1
    await session.commit()
    return await view(session, ledger)


def find(rows: list[dict[str, Any]], value: str | None) -> dict[str, Any]:
    row = next((row for row in rows if row["id"] == value), None)
    if row is None:
        fail("Cet élément n’est plus disponible. Actualisez la liste.", 404)
    assert row is not None
    return row


async def candidates(session: AsyncSession, user_id: uuid.UUID, name: str) -> list[dict[str, Any]]:
    # Similarity only proposes candidates; it NEVER merges or discards a lot.
    lots = await session.scalars(select(KitchenItem).where(KitchenItem.user_id == user_id))
    return [
        KitchenItemView.model_validate(lot).model_dump(mode="json")
        for lot in lots
        if normalized(lot.name) == normalized(name)
        or SequenceMatcher(None, normalized(lot.name), normalized(name)).ratio() >= 0.72
    ]


async def new_lot(session: AsyncSession, user_id: uuid.UUID, food: FoodDetails) -> KitchenItem:
    lot = KitchenItem(
        id=uuid.uuid4(),
        user_id=user_id,
        name=food.name,
        quantity=food.quantity,
        unit=food.unit,
        category=food.category,
        location=food.location,
        date_on=None,
        storage_date=None,
        ingredient_key=None,
        food_id=None,
        status="active",
        version=1,
    )
    session.add(lot)
    await session.flush()
    return lot


async def existing_lot(
    session: AsyncSession, user_id: uuid.UUID, lot_id: uuid.UUID, *, allow_inactive: bool = False
) -> KitchenItem:
    lot = await session.get(KitchenItem, lot_id)
    if lot is None or lot.user_id != user_id or (not allow_inactive and lot.status != "active"):
        fail("Ce lot n’est plus disponible. Vérifiez votre stock.")
    assert lot is not None
    return lot


async def undo_lots(session: AsyncSession, user_id: uuid.UUID, rows: list[dict[str, Any]]) -> None:
    # Validate the whole batch before changing anything. Never overwrite later edits.
    lots = []
    for row in rows:
        lot = await existing_lot(session, user_id, uuid.UUID(row["lot_id"]))
        if lot.version != row["lot_version"]:
            fail(f"{lot.name} a changé depuis son ajout. Vérifiez ce lot avant d’annuler.")
        lots.append(lot)
    for lot in lots:
        lot.status = "removed"
        lot.version += 1


async def add_receipt_line(
    session: AsyncSession,
    ledger: CoursesLedger,
    data: dict[str, Any],
    line: dict[str, Any],
    food: FoodDetails,
    existing_id: uuid.UUID | None = None,
    new_purchase: bool = False,
) -> None:
    if existing_id:
        lot = await existing_lot(session, ledger.user_id, existing_id, allow_inactive=True)
        # An existing-lot selection is explicit; quantities remain as recorded there.
        line.update(status="linked", lot_id=str(lot.id), lot_version=lot.version)
    else:
        matches = await candidates(session, ledger.user_id, food.name)
        if matches and not new_purchase:
            line.update(
                status="review",
                reason="possible_duplicate",
                candidates=matches,
                **food.model_dump(),
            )
            return
        lot = await new_lot(session, ledger.user_id, food)
        line.update(status="added", lot_id=str(lot.id), lot_version=lot.version)
    line.update(food.model_dump())
    # A receipt does not silently check unrelated intentions. It links only an
    # explicitly selected existing purchase lot. Reverse transfer also asks the user.


async def execute(session: AsyncSession, subject: str, command: Command) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    ledger = await ledger_for(session, user.id)
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    old_request = await session.get(CoursesRequest, (user.id, command.request_id))
    if old_request:
        if old_request.digest != digest:
            fail("Cette demande a déjà été utilisée pour une autre action.")
        return await view(session, ledger)
    if command.expected_version != ledger.version:
        fail("La liste a changé. Actualisez-la avant de réessayer.")
    data = copy.deepcopy(ledger.data)
    action = command.action
    if action == "add":
        if command.food is None:
            fail("Renseignez le produit.", 422)
        data["items"].append(
            {
                "id": str(command.request_id),
                **command.food.model_dump(),
                "status": "to_buy",
                "source_key": None,
                "overridden": True,
                "lot_id": None,
            }
        )
    elif action in ("edit", "check", "remove", "restore"):
        item = find(data["items"], command.item_id)
        if item["status"] == "transferred":
            fail("Cet achat est déjà dans votre stock. Modifiez son lot.")
        if action == "edit":
            if not command.food or item["status"] == "removed":
                fail("Renseignez un article actif.", 422)
            item.update(command.food.model_dump())
            item["overridden"] = True
        elif action == "check":
            if command.checked is None or item["status"] == "removed":
                fail("Cet article ne peut pas être coché.", 422)
            item["status"] = "purchased" if command.checked else "to_buy"
        elif action == "remove":
            item["previous_status"] = item["status"]
            item["status"] = "removed"
        else:
            if item["status"] != "removed":
                fail("Cet article a déjà été restauré.")
            item["status"] = item.pop("previous_status", "to_buy")
    elif action == "transfer":
        if not command.lines or len({line.item_id for line in command.lines}) != len(command.lines):
            fail("Choisissez des achats distincts à confirmer.", 422)
        transferred = []
        for entry in command.lines:
            item = find(data["items"], entry.item_id)
            if item["status"] != "purchased":
                fail("Seuls les achats à confirmer peuvent être ajoutés au stock.")
            if entry.existing_lot_id:
                lot = await existing_lot(
                    session, user.id, entry.existing_lot_id, allow_inactive=True
                )
                owned = False
            else:
                if not entry.new_purchase and await candidates(session, user.id, entry.food.name):
                    fail(
                        f"{entry.food.name} existe dans le stock. "
                        "Confirmez s’il s’agit d’un nouvel achat."
                    )
                lot = await new_lot(session, user.id, entry.food)
                owned = True
            item.update(status="transferred", lot_id=str(lot.id))
            transferred.append(
                {
                    "item_id": item["id"],
                    "lot_id": str(lot.id),
                    "lot_version": lot.version,
                    "owned": owned,
                }
            )
        data["transfers"].append(
            {
                "id": str(command.request_id),
                "created_at": now(),
                "status": "completed",
                "lines": transferred,
            }
        )
    elif action == "undo_transfer":
        transfer = find(data["transfers"], command.item_id)
        if transfer["status"] != "completed":
            fail("Cet ajout a déjà été annulé.")
        # Do not erase stock now referenced by a receipt or another transfer.
        owned_ids = {line["lot_id"] for line in transfer["lines"] if line["owned"]}
        if any(
            line.get("lot_id") in owned_ids and line["status"] in ("linked", "added")
            for receipt in data["receipts"]
            if receipt["status"] != "undone"
            for line in receipt["lines"]
        ):
            fail(
                "Un ticket est lié à cet achat. Vérifiez son résultat avant d’annuler le transfert."
            )
        await undo_lots(session, user.id, [line for line in transfer["lines"] if line["owned"]])
        for line in transfer["lines"]:
            find(data["items"], line["item_id"]).update(status="purchased", lot_id=None)
        transfer["status"] = "undone"
    else:
        receipt = find(data["receipts"], command.receipt_id)
        if action == "undo_receipt":
            if receipt["status"] != "completed":
                fail("Ce ticket ne peut pas être annulé dans cet état.")
            owned_rows = [line for line in receipt["lines"] if line["status"] == "added"]
            owned_ids = {line["lot_id"] for line in owned_rows}
            if any(
                line.get("lot_id") in owned_ids and line["status"] == "linked"
                for other in data["receipts"]
                if other["id"] != receipt["id"] and other["status"] != "undone"
                for line in other["lines"]
            ):
                fail("Un autre ticket référence ces lots. Vérifiez les liens avant d’annuler.")
            await undo_lots(session, user.id, owned_rows)
            for item in data["items"]:
                if item.get("lot_id") in owned_ids:
                    item.update(status="purchased", lot_id=None)
            for transfer in data["transfers"]:
                if any(line["lot_id"] in owned_ids for line in transfer["lines"]):
                    transfer["status"] = "undone"
            receipt["status"] = "undone"
        elif action in ("receipt_duplicate", "receipt_distinct"):
            if receipt["status"] != "duplicate_review":
                fail("Ce ticket a déjà été vérifié.")
            if action == "receipt_duplicate":
                receipt.update(status="duplicate", duplicate_of=receipt["possible_duplicate"])
            else:
                receipt["status"] = "completed"
                for line in receipt["lines"]:
                    if line["status"] == "ready":
                        await add_receipt_line(
                            session,
                            ledger,
                            data,
                            line,
                            FoodDetails.model_validate(
                                {key: line[key] for key in FoodDetails.model_fields}
                            ),
                        )
        elif action in ("resolve", "ignore"):
            if receipt["status"] != "completed":
                fail("Ce ticket n’est pas disponible pour modification.")
            line = find(receipt["lines"], command.line_id)
            if line["status"] != "review":
                fail("Cette ligne a déjà été traitée. Actualisez le résultat.")
            if action == "ignore":
                line["status"] = "excluded"
                line["reason"] = "user_ignored"
            else:
                if not command.food:
                    fail("Identifiez l’aliment.", 422)
                await add_receipt_line(
                    session,
                    ledger,
                    data,
                    line,
                    command.food,
                    command.existing_lot_id,
                    command.new_purchase,
                )
        else:
            fail("Action inconnue.", 422)
    ledger.data = data
    ledger.version += 1
    session.add(CoursesRequest(user_id=user.id, request_id=command.request_id, digest=digest))
    await session.commit()
    return await view(session, ledger)
