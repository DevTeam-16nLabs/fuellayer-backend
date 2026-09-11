import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.database import get_session
from fuellayer.modules.recipe_imports import service
from fuellayer.modules.recipe_imports.models import (
    ImportCommand,
    ImportedRecipe,
    ImportJob,
    ImportSource,
)
from fuellayer.modules.recipe_imports.schemas import (
    Correction,
    Fallback,
    ImportInput,
    RecipeData,
    RecipeFields,
    Save,
    Selection,
    Start,
    Version,
)
from fuellayer.modules.recipes.service import build_detail, get_user

Session = Annotated[AsyncSession, Depends(get_session)]
Auth = Annotated[AuthSubject, Depends(require_auth_subject)]
RequestID = Annotated[uuid.UUID, Header(alias="Idempotency-Key")]


def private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["recipe imports"], dependencies=[Depends(private)])


@router.post("/recipe-imports", status_code=202)
async def start(
    payload: Start, request_id: RequestID, auth: Auth, session: Session
) -> dict[str, Any]:
    return await service.start(session, auth.subject, request_id, payload.input)


@router.post("/recipes/imported", status_code=201)
async def manual(request_id: RequestID, auth: Auth, session: Session) -> dict[str, Any]:
    return await service.start(session, auth.subject, request_id, ImportInput(kind="manual"))


@router.get("/recipe-imports")
async def jobs(
    auth: Auth, session: Session, offset: Annotated[int, Query(ge=0)] = 0
) -> dict[str, Any]:
    user = await get_user(session, auth.subject)
    rows = list(
        await session.scalars(
            select(ImportJob)
            .where(ImportJob.user_id == user.id, ImportJob.state.not_in(["cancelled", "expired"]))
            .order_by(ImportJob.created_at.desc())
            .offset(offset)
            .limit(51)
        )
    )
    return {
        "items": [service.job_view(row) for row in rows[:50]],
        "next_offset": offset + 50 if len(rows) > 50 else None,
    }


@router.get("/recipe-imports/{job_id}")
async def status(job_id: uuid.UUID, auth: Auth, session: Session) -> dict[str, Any]:
    user = await get_user(session, auth.subject)
    job = await service.job_for(session, user, job_id)
    result = service.job_view(job)
    result["source_text"] = job.checkpoint.get("text", "")
    return result


@router.get("/recipe-import-commands/{request_id}")
async def command_status(request_id: uuid.UUID, auth: Auth, session: Session) -> dict[str, Any]:
    user = await get_user(session, auth.subject)
    command = await session.get(ImportCommand, (user.id, request_id))
    if command is None:
        service.fail("request_missing", "This request has not been recorded.", 404)
    return command.data


async def job_action(
    session: AsyncSession,
    subject: str,
    job_id: uuid.UUID,
    request_id: uuid.UUID,
    action: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    user = await get_user(session, subject, lock=True)
    operation = action + ":" + str(job_id)
    previous = await service.replay(session, user, request_id, operation, payload)
    if previous is not None:
        return previous
    job = await service.job_for(session, user, job_id)
    if action != "cancel":
        service.version(job.version, payload["expected_version"])
    if action == "cancel":
        if job.recipe_id:
            recipe = await session.get(ImportedRecipe, job.recipe_id)
            if recipe and not recipe.library_saved_at:
                job.recipe_id = None
                await session.delete(recipe)
        job.state, job.input, job.checkpoint = "cancelled", {}, {}
        await session.execute(
            delete(ImportSource).where(
                ImportSource.user_id == user.id, ImportSource.job_id == job.id
            )
        )
    elif action == "selection":
        if job.state != "awaiting_selection" or payload["candidate_id"] not in {
            c["id"] for c in job.checkpoint.get("candidates", [])
        }:
            service.fail("invalid_selection", "Choose one of the recipes found on this page.", 422)
        job.checkpoint = {**job.checkpoint, "selected": payload["candidate_id"]}
        job.state = "queued"
    elif action == "retry":
        if (
            job.state not in ("failed", "retry_wait")
            or job.attempts >= 3
            or not (job.error or {}).get("retryable")
        ):
            service.fail(
                "retry_unavailable", "Use pasted text or manual entry for this import.", 422
            )
        job.state = "queued"
    else:
        incoming = ImportInput.model_validate(payload["input"])
        if incoming.kind == "url" or job.recipe_id:
            service.fail(
                "fallback_unavailable", "Edit the existing recipe or use text/manual entry.", 422
            )
        source = job.input.get("url") or job.input.get("source_url")
        job.input = {**incoming.model_dump(), "source_url": source}
        job.checkpoint, job.attempts = {}, 0
        job.state = "queued"
        if incoming.kind == "manual":
            recipe = await service.create_recipe(
                session, user.id, RecipeData(fields=RecipeFields(source_url=source)), "manual"
            )
            job.recipe_id, job.state = recipe.id, "ready_for_review"
    job.version += 1
    job.lease, job.lease_until, job.error = None, None, None
    job.updated_at, job.next_attempt_at = service.now(), service.now()
    return await service.commit_command(
        session, user, request_id, operation, payload, service.job_view(job)
    )


@router.post("/recipe-imports/{job_id}/selection")
async def choose(
    job_id: uuid.UUID, payload: Selection, request_id: RequestID, auth: Auth, session: Session
) -> dict[str, Any]:
    return await job_action(
        session, auth.subject, job_id, request_id, "selection", payload.model_dump()
    )


@router.post("/recipe-imports/{job_id}/retry")
async def retry(
    job_id: uuid.UUID, payload: Version, request_id: RequestID, auth: Auth, session: Session
) -> dict[str, Any]:
    return await job_action(
        session, auth.subject, job_id, request_id, "retry", payload.model_dump()
    )


@router.post("/recipe-imports/{job_id}/fallback")
async def fallback(
    job_id: uuid.UUID, payload: Fallback, request_id: RequestID, auth: Auth, session: Session
) -> dict[str, Any]:
    return await job_action(
        session, auth.subject, job_id, request_id, "fallback", payload.model_dump()
    )


@router.delete("/recipe-imports/{job_id}", status_code=204)
async def cancel(
    job_id: uuid.UUID, request_id: RequestID, auth: Auth, session: Session
) -> Response:
    await job_action(session, auth.subject, job_id, request_id, "cancel", {})
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})


@router.get("/recipes/{recipe_id}")
async def detail(recipe_id: str, auth: Auth, session: Session) -> dict[str, Any]:
    user = await get_user(session, auth.subject)
    if not recipe_id.startswith("import:"):
        return build_detail(recipe_id).model_dump(mode="json")
    return await service.detail(session, user, await service.owned(session, user, recipe_id))


@router.put("/recipes/imported/{recipe_id}")
async def correct(
    recipe_id: str, payload: Correction, request_id: RequestID, auth: Auth, session: Session
) -> dict[str, Any]:
    return await service.correct(session, auth.subject, recipe_id, request_id, payload)


@router.post("/recipes/imported/{recipe_id}/save")
async def save(
    recipe_id: str, payload: Save, request_id: RequestID, auth: Auth, session: Session
) -> dict[str, Any]:
    return await service.save(session, auth.subject, recipe_id, request_id, payload)


@router.delete("/recipes/imported/{recipe_id}", status_code=204)
async def remove(recipe_id: str, request_id: RequestID, auth: Auth, session: Session) -> Response:
    await service.remove(session, auth.subject, recipe_id, request_id)
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})
