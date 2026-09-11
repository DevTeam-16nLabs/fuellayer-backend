"""Synthetic PostgreSQL smoke test; all changes rolled back, including service commits."""

import asyncio
import runpy
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.core.database import engine
from fuellayer.modules.onboarding.models import GroceryListRecord, StarterPlanRecord, User
from fuellayer.modules.plan import service
from fuellayer.modules.plan.schemas import Occurrence, PortionConfirmRequest, PortionPreviewRequest


async def main() -> None:
    fixture = runpy.run_path("tests/test_plan_replacements.py")
    subject = "portion-transaction-check-" + str(uuid.uuid4())
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(
                bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            ) as session:
                original = fixture["plan_fixture"]()
                session.add(
                    User(
                        clerk_subject=subject,
                        food_preferences=fixture["preference"](),
                        starter_plan=StarterPlanRecord(
                            preview_payload=original, content_version="v1"
                        ),
                        grocery_list=GroceryListRecord(
                            grouped_items=original["grocery"]["main_trip"], content_version="v1"
                        ),
                    )
                )
                await session.commit()
                context = await service.portion_context(
                    session, subject, Occurrence(**fixture["TARGET"])
                )
                preview = await service.preview(
                    session,
                    subject,
                    PortionPreviewRequest(
                        **fixture["TARGET"],
                        personal_portions=1.5,
                        total_portions=4,
                        audience="shared",
                    ),
                )
                body = PortionConfirmRequest(
                    **fixture["TARGET"],
                    personal_portions=1.5,
                    total_portions=4,
                    audience="shared",
                    request_id=uuid.uuid4(),
                    fingerprint=preview["fingerprint"],
                )
                saved = await service.confirm(session, subject, body)
                replay = await service.confirm(session, subject, body)
                assert saved == replay
                assert saved["plan"]["days"][1]["meals"][0]["total_portions"] == 4
                assert (
                    saved["plan"]["days"][1]["meals"][0]["calories_kcal"]
                    == original["days"][1]["meals"][0]["calories_kcal"]
                )
                undone = await service.undo(session, subject, body.request_id)
                assert undone["plan"]["days"] == original["days"]
                assert (await service.status(session, subject, body.request_id))["replacement"][
                    "status"
                ] == "undone"
                print(
                    {
                        "legacy_total": context["total_portions"],
                        "preview": "read-only",
                        "confirm": "passed",
                        "retry": "passed",
                        "undo": "passed",
                    }
                )
        finally:
            await transaction.rollback()
        async with AsyncSession(bind=connection) as session:
            assert await session.scalar(select(User).where(User.clerk_subject == subject)) is None
            print("Synthetic account and all writes rolled back.")
    await engine.dispose()


asyncio.run(main())
