import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.modules.foods.models import CatalogueFood
from fuellayer.modules.kitchen.models import KitchenItem, KitchenMutation
from fuellayer.modules.kitchen.schemas import (
    KitchenInventory,
    KitchenItemCreate,
    KitchenItemStatus,
    KitchenItemUpdate,
    KitchenItemView,
)
from fuellayer.modules.recipes.service import get_user


async def get_inventory(session: AsyncSession, subject: str) -> KitchenInventory:
    user = await get_user(session, subject)
    items = await session.scalars(
        select(KitchenItem)
        .where(KitchenItem.user_id == user.id, KitchenItem.status == "active")
        .order_by(KitchenItem.created_at, KitchenItem.id)
    )
    return KitchenInventory(items=[KitchenItemView.model_validate(item) for item in items])


async def create_item(
    session: AsyncSession, subject: str, payload: KitchenItemCreate
) -> KitchenItemView:
    # Serialize requests per authenticated account, including simultaneous retries.
    user = await get_user(session, subject, lock=True)
    digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    existing = await session.scalar(
        select(KitchenItem).where(
            KitchenItem.user_id == user.id, KitchenItem.request_id == payload.request_id
        )
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise HTTPException(
                409,
                detail={
                    "code": "kitchen_request_conflict",
                    "message": "Cette demande a déjà été utilisée pour un autre produit.",
                },
            )
        return KitchenItemView.model_validate(existing)
    # Accept today's date in every time zone. The client validates its local day.
    latest_today = (datetime.now(UTC) + timedelta(hours=14)).date()
    if payload.storage_date and payload.storage_date > latest_today:
        raise HTTPException(
            422,
            detail={
                "code": "invalid_storage_date",
                "message": "Choisissez aujourd’hui ou une date passée.",
            },
        )
    if payload.food_id is not None:
        food = await session.get(CatalogueFood, payload.food_id)
        if food is None or payload.name != food.name_fr:
            raise HTTPException(
                422,
                detail={
                    "code": "invalid_catalogue_food",
                    "message": "Recherchez à nouveau ce produit ou utilisez un nom libre.",
                },
            )
    item = KitchenItem(
        user_id=user.id,
        **payload.model_dump(),
        request_hash=digest,
        date_on=None,
        ingredient_key=None,
    )
    session.add(item)
    await session.commit()
    return KitchenItemView.model_validate(item)


async def mutate_item(
    session: AsyncSession,
    subject: str,
    item_id: uuid.UUID,
    payload: KitchenItemUpdate | KitchenItemStatus,
) -> KitchenItemView:
    user = await get_user(session, subject, lock=True)
    digest = hashlib.sha256(f"{item_id}:{payload.model_dump_json()}".encode()).hexdigest()
    receipt = await session.get(KitchenMutation, (user.id, payload.request_id))
    if receipt is not None:
        if receipt.request_hash != digest:
            raise HTTPException(
                409,
                detail={
                    "code": "kitchen_request_conflict",
                    "message": "Cette demande a déjà été utilisée.",
                },
            )
        current_item = await session.get(KitchenItem, receipt.item_id)
        if current_item is None or current_item.version != receipt.result["version"]:
            raise HTTPException(
                409,
                detail={
                    "code": "kitchen_version_conflict",
                    "message": "Ce produit a changé depuis cette action. Actualisez le stock.",
                },
            )
        return KitchenItemView.model_validate(receipt.result)
    item = await session.scalar(
        select(KitchenItem).where(KitchenItem.user_id == user.id, KitchenItem.id == item_id)
    )
    if item is None:
        raise HTTPException(
            404,
            detail={"code": "kitchen_item_missing", "message": "Ce produit n’est plus disponible."},
        )
    if item.version != payload.expected_version:
        raise HTTPException(
            409,
            detail={
                "code": "kitchen_version_conflict",
                "message": "Ce produit a changé. Fermez puis rouvrez sa fiche pour le vérifier.",
            },
        )
    if isinstance(payload, KitchenItemUpdate):
        if item.status != "active":
            raise HTTPException(
                409,
                detail={
                    "code": "kitchen_item_inactive",
                    "message": "Ce produit a déjà été retiré du stock.",
                },
            )
        latest_today = (datetime.now(UTC) + timedelta(hours=14)).date()
        if payload.storage_date and payload.storage_date > latest_today:
            raise HTTPException(
                422,
                detail={
                    "code": "invalid_storage_date",
                    "message": "Choisissez aujourd’hui ou une date passée.",
                },
            )
        if payload.name != item.name:
            # A renamed lot must not keep an unrelated catalogue/recipe association.
            item.food_id = None
            item.ingredient_key = None
        for field, value in payload.model_dump(exclude={"request_id", "expected_version"}).items():
            if field != "category" or field in payload.model_fields_set:
                setattr(item, field, value)
    else:
        # Restore only the exact retired revision returned to the caller; never apply
        # an old undo to a subsequently edited, removed or restored lot.
        if (payload.status == "active") == (item.status == "active"):
            raise HTTPException(
                409,
                detail={
                    "code": "kitchen_status_conflict",
                    "message": "L’état de ce produit a changé.",
                },
            )
        item.status = payload.status
    item.version += 1
    await session.flush()
    result = KitchenItemView.model_validate(item)
    session.add(
        KitchenMutation(
            user_id=user.id,
            request_id=payload.request_id,
            item_id=item.id,
            request_hash=digest,
            result=result.model_dump(mode="json"),
        )
    )
    await session.commit()
    return result
