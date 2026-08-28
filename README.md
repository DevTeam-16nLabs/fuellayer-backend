# FuelLayer Backend

FastAPI backend for FuelLayer. The onboarding v1 vertical slice provides a public,
stateless starter-plan preview and an authenticated atomic completion workflow.

## Requirements

- Python 3.13
- PostgreSQL 17 (or Docker)

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
docker compose up -d db
alembic upgrade head
fastapi dev src/fuellayer/main.py
```

The API will be available at `http://localhost:8000`, with interactive documentation at `/docs`.
The local PostgreSQL container uses host port `5433` to avoid common conflicts with a
system PostgreSQL instance on `5432`.

## Commands

```bash
pytest
ruff check .
ruff format --check .
alembic upgrade head
```

## Architecture

The backend is a modular monolith. `src/fuellayer/modules/onboarding/` contains the v1
Pydantic contracts, versioned deterministic planning engine, curated meal catalog, SQL
models, and completion service. Clerk verification and external reconciliation remain at
explicit package boundaries.

Implemented interfaces:

- `POST /api/v1/onboarding/preview` — public, stateless, rate-limited plan preview
- `POST /api/v1/onboarding/complete` — Clerk-authenticated and idempotent atomic save
- `GET /api/v1/me/bootstrap` — local onboarding status and initial Today data
- `DELETE /api/v1/me` — local deletion plus Clerk deletion request
- `POST /api/v1/integrations/clerk/webhook` — signature-verified reconciliation

The engine does not call an LLM. Allergens are hard exclusions, unspecified equation input
uses the midpoint of both Mifflin–St Jeor constants, and constrained catalog results are
returned explicitly. Calculation constants, nutrition data, warnings, and safety bounds
must receive registered-dietitian review before production exposure.
