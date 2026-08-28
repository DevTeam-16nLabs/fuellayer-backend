from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from svix.webhooks import Webhook, WebhookVerificationError

from fuellayer.core.config import settings
from fuellayer.core.database import get_session
from fuellayer.modules.onboarding.service import process_clerk_webhook

router = APIRouter(prefix="/integrations")


@router.post("/clerk/webhook")
async def clerk_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    if not settings.clerk_webhook_signing_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "webhook_not_configured",
                "message": "Clerk webhook is not configured.",
            },
        )
    body = await request.body()
    try:
        event: dict[str, Any] = Webhook(settings.clerk_webhook_signing_secret).verify(
            body, dict(request.headers)
        )
    except WebhookVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_webhook", "message": "Webhook signature verification failed."},
        ) from exc

    event_id = request.headers.get("svix-id") or request.headers.get("webhook-id")
    event_type = event.get("type")
    data = event.get("data")
    if not event_id or not isinstance(event_type, str) or not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_webhook_payload", "message": "Webhook payload is incomplete."},
        )
    processed = await process_clerk_webhook(session, event_id, event_type, data)
    return {"processed": processed}
