import logging
from dataclasses import dataclass
from typing import NoReturn

from clerk_backend_api import Clerk
from clerk_backend_api.security.types import AuthenticateRequestOptions
from fastapi import HTTPException, Request, status
from starlette.concurrency import run_in_threadpool

from fuellayer.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthSubject:
    subject: str
    session_id: str | None = None


def _invalid_session(reason: str) -> NoReturn:
    # Only a fixed diagnostic code: never log JWTs, claims, cookies or user identifiers.
    logger.warning("Clerk session rejected: %s", reason)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_session", "message": "A valid FuelLayer session is required."},
    )


def _authenticate_clerk_request(request: Request) -> AuthSubject:
    if not settings.clerk_secret_key or (
        settings.environment == "production" and not settings.clerk_jwt_issuer
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "authentication_not_configured", "message": "Clerk is not configured."},
        )
    options = AuthenticateRequestOptions(
        secret_key=settings.clerk_secret_key,
        jwt_key=settings.clerk_jwt_key,
        # The Python SDK rejects absent azp, including valid native Bearer sessions.
        # Apply the transport-aware origin policy below, after signature verification.
        authorized_parties=None,
    )
    with Clerk(bearer_auth=settings.clerk_secret_key) as clerk:
        request_state = clerk.authenticate_request(request, options)
    if not request_state.is_signed_in or not request_state.payload:
        reason = request_state.reason.value[0] if request_state.reason else "verification-failed"
        _invalid_session(reason)
    payload = request_state.payload
    issuer = payload.get("iss")
    if (
        not isinstance(issuer, str)
        or not issuer
        or (settings.clerk_jwt_issuer and issuer != settings.clerk_jwt_issuer)
    ):
        _invalid_session("token-invalid-issuer")
    # PyJWT verifies these timestamps when present; a session must also contain them.
    if any(type(payload.get(claim)) is not int for claim in ("exp", "iat", "nbf")):
        _invalid_session("token-missing-session-timestamps")
    if payload.get("sts") == "pending":
        _invalid_session("session-pending")

    if "azp" in payload:
        azp = payload["azp"]
        if not isinstance(azp, str) or azp not in settings.clerk_authorized_parties_list:
            _invalid_session("token-invalid-authorized-parties")
    elif not request.headers.get("authorization", "").startswith("Bearer ") or (
        "origin" in request.headers
    ):
        # Cookie/browser sessions cannot take the native origin exception.
        _invalid_session("token-missing-authorized-party")

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        _invalid_session("token-invalid-subject")
    session_id = payload.get("sid")
    if not isinstance(session_id, str) or not session_id:
        _invalid_session("token-missing-session-id")
    return AuthSubject(subject=subject, session_id=session_id)


async def require_auth_subject(request: Request) -> AuthSubject:
    return await run_in_threadpool(_authenticate_clerk_request, request)


async def delete_clerk_user(subject: str) -> None:
    if not settings.clerk_secret_key:
        raise RuntimeError("Clerk is not configured")
    async with Clerk(bearer_auth=settings.clerk_secret_key) as clerk:
        await clerk.users.delete_async(user_id=subject)
