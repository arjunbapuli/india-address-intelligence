# Deployment

## Resource needs (measured)

| Item | Value |
|---|---|
| RAM, API process with full India Post gazetteer | ~340 MB (peak ~340 MB during load) |
| Startup | ~5 s to load the gazetteer; first-ever start also seeds the DB (~20-30 s) |
| Database size | ~95 MB (Postgres, India Post + curated) |
| CPU | Single-threaded per request; 1-5 ms per resolution |

Add roughly 300-700 MB of RAM for an OpenStreetMap zone import.

## Recommended: Render (lowest effort)

The repo includes `render.yaml` (Blueprint): a Docker web service plus managed Postgres.

1. Push the repo to GitHub (done).
2. Render dashboard → **New → Blueprint** → select the repo → Apply.
3. Render creates the database, injects `DATABASE_URL` and generates an `API_KEYS` value (find it under the service's Environment tab).
4. On first boot the entrypoint runs `alembic upgrade head`, seeds India Post data, and starts uvicorn. Health check: `/health`.
5. Test: `curl https://<service>.onrender.com/health`.

Approximate cost (check Render's current pricing): web service *Starter* (512 MB) about $7/month; Postgres *basic-256mb* about $6/month. Total around $13/month. Use *Standard* (2 GB) if you import OSM.

## Alternative: Fly.io (Mumbai region)

```bash
fly launch --no-deploy            # uses fly.toml (region bom, 1 GB VM, scale-to-zero)
fly postgres create --name address-intel-db --region bom
fly postgres attach address-intel-db
fly secrets set API_KEYS=$(openssl rand -hex 24) ENVIRONMENT=production
fly deploy
```

Scale-to-zero keeps cost low but adds a cold start (~10 s for gazetteer load) after idle.

## Alternative: AWS

App Runner or ECS Fargate (1 vCPU / 1 GB) plus RDS Postgres `db.t4g.micro`. More setup and roughly $30-40/month. Worth it only when you need VPC integration with other AWS systems.

## Any Docker host

```bash
docker build -t address-intel .
docker run -p 8000:8000 -e DATABASE_URL=postgresql://user:pass@host:5432/db \
  -e API_KEYS=change-me -e ENVIRONMENT=production address-intel
```

## Production configuration

| Variable | Production value |
|---|---|
| `ENVIRONMENT` | `production` (forces auth) |
| `API_KEYS` | long random keys, comma-separated |
| `DATABASE_URL` | managed Postgres URL (`postgres://` and `postgresql://` are both accepted) |
| `STORE_ADDRESS_TEXT` | `false` |
| `DATA_RETENTION_DAYS` | per your policy (default 30) |
| `WEB_CONCURRENCY` | `1` per 512 MB of RAM (each worker loads its own gazetteer) |
| `DOCS_ENABLED` | `true` or `false` (Swagger) |

## Scaling notes

- Each uvicorn worker holds its own gazetteer (~340 MB). Scale by instances or workers according to RAM.
- Rate limiting is in-process. With more than one instance, move it to Redis (same `try_acquire` interface in `app/core/ratelimit.py`).
- Data updates: run `python -m scripts.ingest` (or `--osm`) as a one-off job against the production DB, then restart the service.
- Metrics at `/metrics` are per process; ship them to Prometheus/OpenTelemetry when you need dashboards.

## Deployment status of this prototype

Not deployed: the development session had no Render/Fly/AWS credentials. The container entrypoint (migrate → seed → serve) was verified natively against an empty Postgres 16. The Docker image itself wasn't built because Docker Hub was unreachable from the sandbox, so run `docker compose up` locally once to confirm.
