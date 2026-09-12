from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fuellayer.api.v1.system import migration_head
from fuellayer.core.config import Settings
from fuellayer.core.database import get_session
from fuellayer.main import create_app


@pytest.mark.parametrize("prefix", ["postgres://", "postgresql://", "postgresql+asyncpg://"])
def test_managed_postgres_urls_keep_escaped_credentials(prefix: str) -> None:
    config = Settings(_env_file=None, database_url=prefix + "u:p%25%40@db:5432/staging")
    assert config.database_url == "postgresql+asyncpg://u:p%25%40@db:5432/staging"


@pytest.mark.asyncio
async def test_readiness_requires_migrations_and_catalogue(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/readiness.db")
    factory = async_sessionmaker(engine)
    application = create_app()

    async def sessions():
        async with factory() as session:
            yield session

    application.dependency_overrides[get_session] = sessions
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/health")).status_code == 200
            assert (await client.get("/api/v1/ready")).status_code == 503
            async with engine.begin() as conn:
                await conn.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
                await conn.execute(text("CREATE TABLE catalogue_foods (id TEXT PRIMARY KEY)"))
                await conn.execute(text("INSERT INTO alembic_version VALUES ('outdated')"))
                await conn.execute(text("INSERT INTO catalogue_foods VALUES ('ciqual:1')"))
            assert (await client.get("/api/v1/ready")).status_code == 503
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE alembic_version SET version_num = :head"),
                    {"head": migration_head()},
                )
                await conn.execute(text("DELETE FROM catalogue_foods"))
            assert (await client.get("/api/v1/ready")).status_code == 503
            async with engine.begin() as conn:
                await conn.execute(text("INSERT INTO catalogue_foods VALUES ('ciqual:1')"))
            response = await client.get("/api/v1/ready")
            assert response.status_code == 200
            assert response.json()["migration"] == migration_head()
            assert response.json()["status"] == "ready"
    finally:
        await engine.dispose()
