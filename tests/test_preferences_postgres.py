"""Opt-in integration tests: temporary schema in the configured local PostgreSQL DB."""

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fuellayer.core.config import settings
from fuellayer.core.database import Base
from fuellayer.modules.onboarding.models import BodyMeasurement, PreferenceReceipt
from fuellayer.modules.onboarding.service import complete_onboarding
from fuellayer.modules.preferences.schemas import PreviewRequest, SaveRequest
from fuellayer.modules.preferences.service import preview, read, save
from test_onboarding import answers_v2

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("FUELLAYER_TEST_POSTGRES") != "1", reason="requires opt-in local PostgreSQL"
    ),
]


@pytest_asyncio.fixture
async def postgres():
    schema = "preferences_test_" + uuid.uuid4().hex
    admin = create_async_engine(settings.database_url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        settings.database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            await complete_onboarding(db, "test-owner", "initial", answers_v2())
            await complete_onboarding(db, "other-owner", "initial", answers_v2())
        yield factory
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def command(factory, changes):
    async with factory() as db:
        current = await read(db, "test-owner")
        request = PreviewRequest(expected_revision=current["revision"], changes=changes)
        impact = await preview(db, "test-owner", request)
        return SaveRequest(**request.model_dump(), fingerprint=impact["fingerprint"])


async def submit(factory, key, body):
    async with factory() as db:
        try:
            return await save(db, "test-owner", key, body)
        except HTTPException as exc:
            return exc


async def test_simultaneous_duplicate_requests_commit_once(postgres):
    body = await command(postgres, {"profile": {"weight_kg": 82}})
    responses = await asyncio.gather(*(submit(postgres, "same-save", body) for _ in range(4)))
    assert all(r == responses[0] for r in responses)
    assert responses[0]["revision"] == 1
    async with postgres() as db:
        assert await read(db, "test-owner") == responses[0]
        assert (await read(db, "other-owner"))["revision"] == 0
        assert await db.scalar(select(func.count()).select_from(PreferenceReceipt)) == 1
        assert await db.scalar(select(func.count()).select_from(BodyMeasurement)) == 3


async def test_simultaneous_different_edits_require_explicit_rebase(postgres):
    first = await command(postgres, {"units": "imperial"})
    second = await command(postgres, {"food": {"cooking_time": "15_min"}})
    responses = await asyncio.gather(
        submit(postgres, "first", first), submit(postgres, "second", second)
    )
    saved = [r for r in responses if isinstance(r, dict)]
    conflicts = [r for r in responses if isinstance(r, HTTPException)]
    assert len(saved) == len(conflicts) == 1
    assert conflicts[0].status_code == 409
    assert conflicts[0].detail["code"] == "preferences_conflict"
    async with postgres() as db:
        current = await read(db, "test-owner")
        assert current == saved[0] and current["revision"] == 1
        assert await db.scalar(select(func.count()).select_from(PreferenceReceipt)) == 1
