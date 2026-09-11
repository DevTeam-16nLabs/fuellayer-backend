import asyncio
import logging
import uuid
from datetime import UTC, timedelta
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fuellayer.core.database import session_factory
from fuellayer.modules.recipe_imports import extract, service
from fuellayer.modules.recipe_imports.fetch import ImportFailure, fetch_page, normalize_url
from fuellayer.modules.recipe_imports.models import (
    ImportCommand,
    ImportedRecipe,
    ImportJob,
    ImportSource,
)
from fuellayer.modules.recipe_imports.schemas import RecipeData
from fuellayer.modules.recipes.service import get_user

logger = logging.getLogger(__name__)
ACTIVE = ("fetching", "parsing", "structuring", "validating")


async def checkpoint(
    factory: async_sessionmaker[AsyncSession],
    subject: str,
    job_id: uuid.UUID,
    lease: str,
    state: str,
    data: dict[str, Any],
) -> bool:
    async with factory() as session:
        user = await get_user(session, subject, lock=True)
        job = await service.job_for(session, user, job_id)
        if job.lease != lease:
            return False
        job.state, job.checkpoint, job.updated_at = state, data, service.now()
        job.lease_until = service.now() + timedelta(minutes=3)
        await session.commit()
        return True


async def process(
    job_id: uuid.UUID, factory: async_sessionmaker[AsyncSession] = session_factory
) -> None:
    from fuellayer.modules.onboarding.models import User

    lease = str(uuid.uuid4())
    async with factory() as session:
        initial = await session.get(ImportJob, job_id)
        if initial is None:
            return
        user = await session.get(User, initial.user_id)
        if user is None:
            return
        subject = user.clerk_subject
        user = await get_user(session, subject, lock=True)
        await session.refresh(initial)
        if initial.state not in (*ACTIVE, "queued", "retry_wait"):
            return
        if initial.lease_until and initial.lease_until.replace(tzinfo=UTC) > service.now():
            return
        if initial.next_attempt_at.replace(tzinfo=UTC) > service.now():
            return
        if initial.attempts >= 3:
            initial.state = "failed"
            initial.error = {
                "code": "attempts_exhausted",
                "message": (
                    "Processing was interrupted repeatedly. Paste text or enter details manually."
                ),
                "retryable": False,
            }
            await session.commit()
            return
        initial.lease, initial.lease_until = lease, service.now() + timedelta(minutes=3)
        initial.state, initial.attempts = "fetching", initial.attempts + 1
        initial.error, initial.updated_at = None, service.now()
        payload, saved = dict(initial.input), dict(initial.checkpoint)
        await session.commit()
    try:
        url = payload.get("url") or payload.get("source_url")
        if "text" not in saved:
            if payload["kind"] == "url":
                page = await fetch_page(payload["url"])
                recipes, text = extract.candidates(page.text)
                saved = {"text": text, "url": page.url, "recipes": recipes}
                saved["candidates"] = [
                    {
                        "id": extract.candidate_id(r),
                        "title": extract.text_only(r.get("name")) or "Untitled recipe",
                    }
                    for r in recipes
                ]
            else:
                saved = {"text": payload.get("text", ""), "url": url, "recipes": []}
            if not await checkpoint(factory, subject, job_id, lease, "parsing", saved):
                return
        recipes = saved.get("recipes", [])
        if len(recipes) > 1 and not saved.get("selected"):
            async with factory() as session:
                user = await get_user(session, subject, lock=True)
                job = await service.job_for(session, user, job_id)
                if job.lease == lease:
                    job.state, job.lease, job.lease_until = "awaiting_selection", None, None
                    job.version += 1
                    await session.commit()
            return
        if recipes:
            raw = next(
                (r for r in recipes if extract.candidate_id(r) == saved.get("selected")), recipes[0]
            )
            result = extract.source_recipe(raw, saved.get("url"))
            if not result.ingredients and not result.steps:
                result = await extract.text_recipe(saved["text"], saved.get("url"))
        else:
            if not await checkpoint(factory, subject, job_id, lease, "structuring", saved):
                return
            result = await extract.text_recipe(saved["text"], saved.get("url"))
        result = RecipeData.model_validate(result)
        if not await checkpoint(factory, subject, job_id, lease, "validating", saved):
            return
        async with factory() as session:
            user = await get_user(session, subject, lock=True)
            job = await service.job_for(session, user, job_id)
            if job.lease != lease:
                return
            keys = [service.digest(["content", result.model_dump()])]
            if saved.get("url"):
                keys.append(service.digest(["url", normalize_url(saved["url"])]))
            duplicate = None
            for key in keys:
                alias = await session.get(ImportSource, (user.id, key))
                if alias and alias.job_id != job.id:
                    prior = await service.job_for(session, user, alias.job_id)
                    if prior.recipe_id:
                        duplicate = prior
            if duplicate:
                job.recipe_id = duplicate.recipe_id
            else:
                recipe = await service.create_recipe(session, user.id, result, "source")
                job.recipe_id = recipe.id
                for key in keys:
                    if await session.get(ImportSource, (user.id, key)) is None:
                        session.add(ImportSource(user_id=user.id, key=key, job_id=job.id))
            job.state, job.lease, job.lease_until, job.error = "ready_for_review", None, None, None
            # Retain bounded evidence, never full fetched HTML or arbitrary unrelated content.
            job.checkpoint = {
                "text": saved.get("text", "")[:50000],
                "url": saved.get("url"),
                "duplicate": bool(duplicate),
            }
            job.version += 1
            job.updated_at = service.now()
            await session.commit()
    except Exception as exc:
        failure = (
            exc
            if isinstance(exc, ImportFailure)
            else ImportFailure(
                "extraction_invalid",
                "The recipe could not be extracted. Try again or enter details manually.",
                True,
            )
        )
        logger.info("recipe_import_failed job=%s code=%s", job_id, failure.code)
        async with factory() as session:
            user = await get_user(session, subject, lock=True)
            job = await service.job_for(session, user, job_id)
            if job.lease != lease:
                return
            retry = failure.retryable and job.attempts < 3
            job.state = "retry_wait" if retry else "failed"
            job.error = {"code": failure.code, "message": failure.message, "retryable": retry}
            job.next_attempt_at = service.now() + timedelta(seconds=5 * 2**job.attempts)
            job.lease, job.lease_until = None, None
            job.version += 1
            job.updated_at = service.now()
            await session.commit()


async def cleanup(factory: async_sessionmaker[AsyncSession] = session_factory) -> None:
    async with factory() as session:
        jobs = list(
            await session.scalars(
                select(ImportJob).where(ImportJob.updated_at < service.now() - timedelta(days=7))
            )
        )
        for job in jobs:
            # Keep saved-recipe aliases indefinitely: retrying an old URL must not duplicate it.
            recipe = await session.get(ImportedRecipe, job.recipe_id) if job.recipe_id else None
            retained = recipe is not None and recipe.library_saved_at is not None
            job.input, job.checkpoint, job.lease, job.lease_until = {}, {}, None, None
            job.updated_at = service.now()
            if retained:
                job.state = "ready_for_review"
                continue
            if job.state != "cancelled":
                job.state = "expired"
            if recipe:
                # Other jobs can point at the same unsaved draft.
                for linked in await session.scalars(
                    select(ImportJob).where(ImportJob.recipe_id == recipe.id)
                ):
                    linked.recipe_id = None
                    linked.state = "expired"
                await session.flush()
                await session.delete(recipe)
            await session.execute(delete(ImportSource).where(ImportSource.job_id == job.id))
        for command in await session.scalars(
            select(ImportCommand).where(
                ImportCommand.created_at < service.now() - timedelta(days=30)
            )
        ):
            command.data = {"expired": True}
        await session.commit()


async def run() -> None:
    """Run one supervised worker; database leases make restart/replay safe."""
    cycles = 0
    while True:
        async with session_factory() as session:
            ids = list(
                await session.scalars(
                    select(ImportJob.id)
                    .where(
                        ImportJob.state.in_(["queued", "retry_wait", *ACTIVE]),
                        ImportJob.next_attempt_at <= service.now(),
                        or_(ImportJob.lease_until.is_(None), ImportJob.lease_until < service.now()),
                    )
                    .order_by(ImportJob.created_at)
                    .limit(2)
                )
            )
        for job_id in ids:
            try:
                await process(job_id)
            except Exception:
                # Account/job deletion or a database outage must not kill the supervisor.
                logger.exception("recipe_import_worker_interrupted job=%s", job_id)
        if cycles % 300 == 0:
            await cleanup()
        cycles += 1
        await asyncio.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
