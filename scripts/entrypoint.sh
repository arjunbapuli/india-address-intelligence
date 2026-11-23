#!/bin/sh
# Container entrypoint: migrate, seed if empty, then serve.
set -e
echo "[entrypoint] running database migrations"
python -m alembic upgrade head
echo "[entrypoint] seeding geographic data if the database is empty (first run takes ~30s)"
python -m scripts.ingest --if-empty
if [ -n "$OSM_IMPORT_PATH" ] && [ -f "$OSM_IMPORT_PATH" ]; then
  echo "[entrypoint] importing OpenStreetMap extract $OSM_IMPORT_PATH"
  python -m scripts.ingest --if-empty --osm "$OSM_IMPORT_PATH"
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-1}" --proxy-headers --forwarded-allow-ips='*'
