from fastapi import APIRouter, HTTPException, Request, status

from fuellayer.core.config import settings
from fuellayer.modules.onboarding.rate_limit import SlidingWindowRateLimiter
from fuellayer.modules.onboarding.schemas import StoreSearchRequest, StoreSearchResponse
from fuellayer.modules.onboarding.stores import StoreProviderUnavailableError, search_stores

router = APIRouter(prefix="/stores")
store_search_rate_limiter = SlidingWindowRateLimiter(settings.store_search_rate_limit)


@router.post("/search", response_model=StoreSearchResponse)
async def find_stores(payload: StoreSearchRequest, request: Request) -> StoreSearchResponse:
    client_key = request.client.host if request.client else "unknown"
    if not store_search_rate_limiter.allow(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "store_search_rate_limited",
                "message": "Too many store searches. Wait a moment and try again.",
            },
        )
    try:
        return await search_stores(payload)
    except StoreProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "store_search_unavailable", "message": str(exc)},
        ) from exc
