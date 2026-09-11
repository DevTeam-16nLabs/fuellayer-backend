import uuid
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.courses import receipts, service
from fuellayer.modules.courses.schemas import Command, ReceiptUpload

router = APIRouter(prefix="/courses", tags=["courses"])
Auth = Annotated[AuthSubject, Depends(require_auth_subject)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("")
async def get_courses(
    auth: Auth, session: Session, response: Response, tasks: BackgroundTasks
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    state = await service.read(session, auth.subject)
    for receipt in state["receipts"]:
        if receipt["status"] in ("queued", "processing"):
            tasks.add_task(receipts.process, uuid.UUID(receipt["id"]))
    return state


@router.post("/actions")
async def mutate_courses(
    payload: Command, auth: Auth, session: Session, response: Response
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.execute(session, auth.subject, payload)


@router.post("/receipts", status_code=202)
async def add_receipt(
    payload: ReceiptUpload, auth: Auth, session: Session, response: Response, tasks: BackgroundTasks
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    result = await receipts.upload(session, auth.subject, payload)
    tasks.add_task(receipts.process, uuid.UUID(result["receipt_id"]))
    return result
