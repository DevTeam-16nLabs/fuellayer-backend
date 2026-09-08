from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.modules.kitchen.models import KitchenItem
from fuellayer.modules.kitchen.schemas import KitchenInventory, KitchenItemView
from fuellayer.modules.recipes.service import get_user


async def get_inventory(session: AsyncSession, subject: str) -> KitchenInventory:
    user = await get_user(session, subject)
    items = await session.scalars(
        select(KitchenItem)
        .where(KitchenItem.user_id == user.id)
        .order_by(KitchenItem.created_at, KitchenItem.id)
    )
    return KitchenInventory(items=[KitchenItemView.model_validate(item) for item in items])
