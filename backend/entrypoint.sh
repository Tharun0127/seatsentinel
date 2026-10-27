#!/bin/sh
set -e

echo "[entrypoint] running migrations"
alembic upgrade head

echo "[entrypoint] seeding events and seats (idempotent)"
python -m scripts.seed

echo "[entrypoint] starting uvicorn with ${WEB_WORKERS:-4} workers"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_WORKERS:-4}" --no-access-log
