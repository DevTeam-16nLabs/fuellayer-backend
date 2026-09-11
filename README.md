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

## Continuous integration

GitHub Actions runs on pull requests, pushes to `main`, and manual dispatches.
The workflow uses Python 3.13.7, uv 0.12.13, `uv.lock`, and PostgreSQL 17.
Install the same dependency set locally with `uv sync --locked --extra dev`.

The checks are `Backend quality` (Ruff lint/format and strict MyPy) and
`Backend tests and migrations` (SQLite/PostgreSQL tests, the real Alembic chain,
and two catalogue imports with identical results). Add both checks to the `main`
branch ruleset after publishing the workflow to require them before merging.

To reproduce the database checks, use a **disposable** PostgreSQL database whose
name begins with `fuellayer_ci`, then run:

```bash
export ENVIRONMENT=test
export DATABASE_URL=postgresql+asyncpg://fuellayer_ci:ci-test-only@127.0.0.1:5432/fuellayer_ci
export FUELLAYER_TEST_POSTGRES=1
export FUELLAYER_DIARY_POSTGRES_TEST=1
uv run --no-sync python scripts/check_ci_database.py
uv run --no-sync pytest --deselect=tests/test_diary_postgres.py::test_real_http_mobile_engine
```

The single deselected test needs the private mobile source. It runs in the mobile
repository's CI, with this public backend checked out beside `fuellayer-mobile/`.
When both repositories are present locally and a compatible Node version is on
`PATH`, omit `--deselect` to run all tests, including the HTTP/mobile engine.
No test needs real Clerk, Apple, Google, or AI credentials. JUnit results are kept
as GitHub artifacts for 14 days.

Merge the backend CI changes, including `uv.lock`, before enabling the mobile CI:
the mobile integration job deliberately requires the backend's lockfile.

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

## Recipe imports

Recipe imports require migration `20260908_11` and a separately supervised worker. See [recipe import operations and API contracts](docs/recipe-imports.md) for setup, supported-source boundaries, recovery, and validation.
