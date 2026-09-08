#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
MOBILE_ENV="$BACKEND_DIR/../fuellayer-mobile/.env.local"

cd "$BACKEND_DIR"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
elif [ -f "$MOBILE_ENV" ]; then
  CLERK_VALUE=$(sed -n 's/^CLERK_SECRET_KEY=//p' "$MOBILE_ENV" | tail -n 1)
  if [ -n "$CLERK_VALUE" ]; then
    export CLERK_SECRET_KEY="$CLERK_VALUE"
  fi
fi

if docker compose version >/dev/null 2>&1; then
  docker compose up -d db
else
  docker-compose up -d db
fi
.venv/bin/alembic upgrade head

echo "FuelLayer API available to this Mac and nearby devices on port 8000."
exec .venv/bin/uvicorn fuellayer.main:app \
  --app-dir src \
  --host 0.0.0.0 \
  --port 8000 \
  --reload
