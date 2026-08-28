from dataclasses import dataclass

from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions
from fastapi import HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from fuellayer.core.config import settings


@dataclass(frozen=True)
class AuthSubject:
    subject: str
    session_id: str | None = None


def _authenticate_clerk_request(request: Request) -> AuthSubject:
    if not settings.clerk_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "authentication_not_configured", "message": "Clerk is not configured."},
        )
    options = AuthenticateRequestOptions(
        secret_key=settings.clerk_secret_key,
        jwt_key=settings.clerk_jwt_key,
        authorized_parties=settings.clerk_authorized_parties_list,
    )
    with Clerk(bearer_auth=settings.clerk_secret_key) as clerk:
        request_state = clerk.authenticate_request(request, options)
    if not request_state.is_signed_in or not request_state.payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_session", "message": "A valid FuelLayer session is required."},
        )
    subject = request_state.payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_subject", "message": "The session has no stable subject."},
        )
    session_id = request_state.payload.get("sid")
    return AuthSubject(
        subject=subject,
        session_id=session_id if isinstance(session_id, str) else None,
    )


async def require_auth_subject(request: Request) -> AuthSubject:
    return await run_in_threadpool(_authenticate_clerk_request, request)


async def delete_clerk_user(subject: str) -> None:
    if not settings.clerk_secret_key:
        raise RuntimeError("Clerk is not configured")
    async with Clerk(bearer_auth=settings.clerk_secret_key) as clerk:
        await clerk.users.delete_async(user_id=subject)
