from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.kitchen.schemas import KitchenInventory
from fuellayer.modules.kitchen.service import get_inventory

router = APIRouter(prefix="/kitchen", tags=["kitchen"])


@router.get("/inventory", response_model=KitchenInventory)
async def inventory(
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
) -> KitchenInventory:
    response.headers["Cache-Control"] = "private, no-store"
    return await get_inventory(session, auth.subject)
