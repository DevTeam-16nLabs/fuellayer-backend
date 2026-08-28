from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.onboarding.schemas import AccountDeletionResponse, BootstrapResponse
from fuellayer.modules.onboarding.service import delete_account, get_bootstrap

router = APIRouter(prefix="/me")


@router.get("/bootstrap", response_model=BootstrapResponse)
async def bootstrap_me(
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BootstrapResponse:
    return await get_bootstrap(session, auth.subject)


@router.delete("", response_model=AccountDeletionResponse)
async def remove_me(
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountDeletionResponse:
    return await delete_account(session, auth.subject)
