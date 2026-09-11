import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.kitchen.schemas import (
    KitchenInventory,
    KitchenItemCreate,
    KitchenItemStatus,
    KitchenItemUpdate,
    KitchenItemView,
)
from fuellayer.modules.kitchen.service import create_item, get_inventory, mutate_item

router = APIRouter(prefix="/kitchen", tags=["kitchen"])


@router.get("/inventory", response_model=KitchenInventory)
async def inventory(
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
) -> KitchenInventory:
    response.headers["Cache-Control"] = "private, no-store"
    return await get_inventory(session, auth.subject)


@router.post("/items", response_model=KitchenItemView, status_code=201)
async def add_item(
    payload: KitchenItemCreate,
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
) -> KitchenItemView:
    response.headers["Cache-Control"] = "private, no-store"
    return await create_item(session, auth.subject, payload)


@router.patch("/items/{item_id}", response_model=KitchenItemView)
async def edit_item(
    item_id: uuid.UUID,
    payload: KitchenItemUpdate,
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
) -> KitchenItemView:
    response.headers["Cache-Control"] = "private, no-store"
    return await mutate_item(session, auth.subject, item_id, payload)


@router.post("/items/{item_id}/status", response_model=KitchenItemView)
async def change_item_status(
    item_id: uuid.UUID,
    payload: KitchenItemStatus,
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
) -> KitchenItemView:
    response.headers["Cache-Control"] = "private, no-store"
    return await mutate_item(session, auth.subject, item_id, payload)
