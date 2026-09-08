from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.onboarding.schemas import (
    AccountDeletionResponse,
    BootstrapResponse,
    PlanningProfileUpgrade,
)
from fuellayer.modules.onboarding.service import (
    delete_account,
    get_bootstrap,
    upgrade_planning_profile,
)

router = APIRouter(prefix="/me")


@router.get("/bootstrap", response_model=BootstrapResponse)
async def bootstrap_me(
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BootstrapResponse:
    return await get_bootstrap(session, auth.subject)


@router.put("/planning-profile", response_model=BootstrapResponse)
async def update_planning_profile(
    payload: PlanningProfileUpgrade,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BootstrapResponse:
    return await upgrade_planning_profile(session, auth.subject, idempotency_key, payload)


@router.delete("", response_model=AccountDeletionResponse)
async def remove_me(
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountDeletionResponse:
    return await delete_account(session, auth.subject)
