# FuelLayer Backend

FastAPI backend for FuelLayer.

This repository is intentionally limited to platform foundations. Product modules will be added as vertical slices after the domain and API contracts are approved.

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
fastapi dev src/fuellayer/main.py
```

The API will be available at `http://localhost:8000`, with interactive documentation at `/docs`.

## Commands

```bash
pytest
ruff check .
ruff format --check .
alembic upgrade head
```

## Architecture

The backend begins as a modular monolith. Domain modules will live under `src/fuellayer/modules/`, while infrastructure and external integrations remain explicit at the package boundary.

