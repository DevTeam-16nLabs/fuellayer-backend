"""Opt-in PostgreSQL + actual HTTP/client engine verification in a disposable schema.
Run FUELLAYER_DIARY_POSTGRES_TEST=1 pytest tests/test_diary_postgres.py -q.
Production auth remains unchanged; test-only principals exist only in this server.
"""

import asyncio
import os
import socket
import uuid
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn
from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import test_diary as checks
from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.config import settings
from fuellayer.core.database import Base, get_session
from fuellayer.main import create_app
from fuellayer.modules.onboarding.service import complete_onboarding
from test_onboarding import answers_v2

pytestmark = pytest.mark.skipif(
    os.getenv("FUELLAYER_DIARY_POSTGRES_TEST") != "1", reason="Opt-in local PostgreSQL integration"
)


@pytest_asyncio.fixture
async def pg():
    admin = create_async_engine(settings.database_url)
    schema = "diary_test_" + uuid.uuid4().hex
    async with admin.begin() as db:
        await db.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_async_engine(
        settings.database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            for who in ["alice", "bob"]:
                await complete_onboarding(db, who, who + "-onboarding", answers_v2())
        app = create_app()

        async def auth(request: Request):
            who = request.headers.get("x-test-user")
            if who not in ("alice", "bob"):
                raise HTTPException(401)
            return AuthSubject(who)

        async def sessions():
            async with factory() as db:
                yield db

        app.dependency_overrides[require_auth_subject] = auth
        app.dependency_overrides[get_session] = sessions
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, factory, app
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        await admin.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "check",
    [
        checks.test_history_crud_snapshot_retry_undo_no_side_effects,
        checks.test_auth_ownership_and_cross_account_ids,
        checks.test_migration_is_idempotent_preserves_ids_and_detects_collision,
        checks.test_concurrency_and_change_cursor_tombstones,
        checks.test_week_missing_macros_zero_and_future,
        checks.test_date_timezone_and_verified_targets,
    ],
)
async def test_postgres_contracts(pg, check):
    await check(pg[:2])


@pytest.mark.asyncio
async def test_real_http_mobile_engine(pg, tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(pg[2], log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started
        script = (
            Path(__file__).resolve().parents[2] / "fuellayer-mobile/scripts/verify-diary-http.ts"
        )
        proc = await asyncio.create_subprocess_exec(
            "node",
            "--experimental-strip-types",
            str(script),
            f"http://127.0.0.1:{port}",
            str(tmp_path / "diary-cache.json"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), 45)
        assert proc.returncode == 0, err.decode() + out.decode()
        print(out.decode())
    finally:
        server.should_exit = True
        await task
        sock.close()
