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
python scripts/import_foods.py
fastapi dev src/fuellayer/main.py
```

The API will be available at `http://localhost:8000`, with interactive documentation at `/docs`.
The local PostgreSQL container uses host port `5433` to avoid common conflicts with a
system PostgreSQL instance on `5432`.

## Physical-device API

Run the device helper before launching FuelLayer on an iPhone:

```bash
./scripts/start-device-api.sh
```

It starts PostgreSQL with either `docker compose` or `docker-compose`, applies migrations,
and serves FastAPI on `0.0.0.0:8000` so a phone
on the same Wi-Fi network can reach the API. Prefer a backend `.env` for Clerk and Google
Places secrets. During local migration only, the helper can reuse a non-public
`CLERK_SECRET_KEY` already present in the ignored mobile `.env.local`; the secret is never
exported to the Expo bundle.

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

## Food catalogue

`GET /api/v1/foods/search?q=banane&locale=fr&limit=25&offset=0` searches a local PostgreSQL index of 3,484 Anses Ciqual 2025 foods. English names and accent-insensitive queries are supported. `GET /api/v1/foods/{food_id}` returns the source facts. These public catalogue endpoints contain no diary or private-food data.

After migrations, run `python scripts/import_foods.py` from the backend directory to upsert the checked-in catalogue. See `data/README.md` for official attribution, pinned export checksums and rebuilding. Unknown/censored nutrients remain null with source notes. USDA, Open Food Facts, image analysis and private diary synchronization are separate upcoming increments.

For Expo Web, `CORS_ORIGINS` is a comma-separated allowlist, defaulting to `http://localhost:8081,http://127.0.0.1:8081`. Native device requests do not need browser CORS. Keep provider secrets in the ignored backend `.env`.
