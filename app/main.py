"""FastAPI application factory.

Startup:
  1. configure JSON logging
  2. ensure tables exist (SQLite quick start; Postgres uses Alembic in the entrypoint)
  3. seed the geographic database if empty (AUTO_SEED=true)
  4. load the in-memory gazetteer
  5. build services (resolver, optional LLM / STT / geocoders)
If seeding or loading fails, the API still starts in a degraded mode and /health says why.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.deps import build_services
from app.api.routes import address, audio, feedback, meta
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger, log_event, request_id_var
from app.core.metrics import metrics
from app.database.session import create_all, session_scope
from app.geography.gazetteer import Gazetteer, get_gazetteer, set_gazetteer
from app.version import API_VERSION

log = get_logger("app")


def _startup_data(app: FastAPI) -> None:
    s = get_settings()
    try:
        create_all()  # no-op for existing tables; Alembic remains the source of truth for Postgres
    except Exception as exc:
        log.error("database_unavailable", extra={"extra_fields": {"error": type(exc).__name__}})
        return
    if s.auto_seed:
        try:
            from app.geography.ingestion.pipeline import database_is_empty, run_core_ingestion

            with session_scope() as sess:
                if database_is_empty(sess):
                    log_event(log, "auto_seed_start")
                    run_core_ingestion(sess)
        except Exception as exc:
            log.exception("auto_seed_failed", extra={"extra_fields": {"error": type(exc).__name__}})
    if s.load_gazetteer_on_startup and get_gazetteer() is None:
        try:
            g = Gazetteer.load_from_db()
            if g.places:
                set_gazetteer(g)
        except Exception as exc:
            log.exception("gazetteer_load_failed", extra={"extra_fields": {"error": type(exc).__name__}})
    g = get_gazetteer()
    if g is not None:
        app.state.services = build_services(g, s)


async def _retention_loop() -> None:
    from app.database.repositories.records import purge_expired

    while True:
        try:
            s = get_settings()
            with session_scope() as sess:
                res = purge_expired(sess, s.data_retention_days)
            if any(res.values()):
                log_event(log, "retention_purge", **res)
        except Exception as exc:  # pragma: no cover - infra dependent
            log.warning("retention_purge_failed", extra={"extra_fields": {"error": type(exc).__name__}})
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level)
    await asyncio.to_thread(_startup_data, app)
    task = asyncio.create_task(_retention_loop()) if s.environment != "test" else None
    log_event(log, "startup_complete", version=API_VERSION, gazetteer_loaded=get_gazetteer() is not None,
              auth_required=s.auth_required)
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title=s.app_name,
        version=API_VERSION,
        description=(
            "Address intelligence for India: turns noisy, spoken, misspelled or code-switched addresses into "
            "structured, geographically validated addresses with evidence-based confidence.\n\n"
            "Authenticate with the `X-API-Key` header. All responses include `X-Request-ID`.\n\n"
            "Geographic data: India Post All-India Pincode Directory (GODL-India) + curated naming data "
            "(+ OpenStreetMap © contributors, ODbL, when imported). See `/v1/coverage`."
        ),
        lifespan=lifespan,
        docs_url="/docs" if s.docs_enabled else None,
        redoc_url="/redoc" if s.docs_enabled else None,
        openapi_url="/openapi.json" if s.docs_enabled else None,
    )
    install_error_handlers(app)
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
                       allow_headers=["*"], expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        rid = rid[:64]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            ms = (time.perf_counter() - t0) * 1000
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path)
            client_id = getattr(request.state, "client_id", None)
            metrics.record_request(f"{request.method} {route_path}", status, ms, client_id)
            # Access log without query string (search queries can contain addresses)
            log_event(log, "request", method=request.method, path=route_path, status=status,
                      latency_ms=round(ms, 2), client=client_id)
            request_id_var.reset(token)

    app.include_router(meta.router)
    app.include_router(address.router)
    app.include_router(feedback.router)
    app.include_router(audio.router)
    return app


app = create_app()
