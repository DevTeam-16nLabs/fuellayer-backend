from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.config import settings
from fuellayer.core.database import get_session
from fuellayer.modules.onboarding.engine import UnderageNotSupportedError, build_preview
from fuellayer.modules.onboarding.rate_limit import SlidingWindowRateLimiter
from fuellayer.modules.onboarding.schemas import (
    BootstrapResponse,
    OnboardingAnswers,
    StarterPlanPreview,
)
from fuellayer.modules.onboarding.service import complete_onboarding

router = APIRouter(prefix="/onboarding")
preview_rate_limiter = SlidingWindowRateLimiter(settings.onboarding_preview_rate_limit)


@router.post("/preview", response_model=StarterPlanPreview)
async def preview_onboarding(
    answers: OnboardingAnswers,
    request: Request,
) -> StarterPlanPreview:
    client_key = request.client.host if request.client else "unknown"
    if not preview_rate_limiter.allow(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "preview_rate_limited",
                "message": "Too many plan previews. Wait a moment and try again.",
            },
        )
    try:
        return build_preview(answers)
    except UnderageNotSupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "underage_not_supported", "message": str(exc)},
        ) from exc


@router.post("/complete", response_model=BootstrapResponse)
async def save_onboarding(
    answers: OnboardingAnswers,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    auth: Annotated[AuthSubject, Depends(require_auth_subject)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BootstrapResponse:
    try:
        return await complete_onboarding(session, auth.subject, idempotency_key, answers)
    except UnderageNotSupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "underage_not_supported", "message": str(exc)},
        ) from exc
