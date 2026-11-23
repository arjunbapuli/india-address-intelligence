"""Service info, health, coverage and metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.metrics import metrics
from app.core.security import authenticate
from app.database.session import db_healthy
from app.geography.gazetteer import get_gazetteer
from app.version import API_VERSION, RESOLVER_VERSION

router = APIRouter(tags=["service"])


@router.get("/", summary="API information")
async def root(request: Request) -> dict:
    s = get_settings()
    g = get_gazetteer()
    return {
        "name": s.app_name,
        "version": API_VERSION,
        "resolver_version": RESOLVER_VERSION,
        "description": "Resolves noisy, spoken or unstructured Indian addresses into validated, structured "
                       "addresses with evidence-based confidence.",
        "capabilities": {
            "resolve": "POST /v1/address/resolve",
            "batch_resolve": "POST /v1/address/resolve/batch",
            "validate": "POST /v1/address/validate",
            "search": "GET /v1/address/search?q=...",
            "pincode_lookup": "GET /v1/pincode/{pincode}",
            "feedback": "POST /v1/feedback",
            "audio_resolve": "POST /v1/address/resolve/audio (requires STT_PROVIDER)",
            "coverage": "GET /v1/coverage",
        },
        "languages": {
            "supported_input": ["English", "Hindi (Devanagari & romanised)", "Hinglish / code-switching"],
            "transliterated_for_matching": ["Kannada", "Tamil", "Telugu", "Malayalam", "Bengali", "Marathi",
                                            "Gujarati", "Punjabi", "Odia"],
        },
        "coverage_summary": (g.coverage()["entities_by_type"] if g else "gazetteer not loaded"),
        "auth": "X-API-Key header" if s.auth_required else "disabled (development)",
        "docs": {"swagger": "/docs" if s.docs_enabled else None, "redoc": "/redoc" if s.docs_enabled else None,
                 "openapi": "/openapi.json" if s.docs_enabled else None},
    }


@router.get("/health", summary="Service health and dependency status")
async def health(request: Request) -> JSONResponse:
    s = get_settings()
    g = get_gazetteer()
    db_ok, db_err = db_healthy()
    svc = getattr(request.app.state, "services", None)
    deps = {
        "database": {"status": "ok" if db_ok else "error", "error": db_err},
        "gazetteer": {"status": "ok" if g else "not_loaded",
                      "entities": len(g.places) if g else 0,
                      "load_seconds": g.stats.load_seconds if g else None},
        "llm": {"status": "enabled" if svc and svc.llm_enabled else "disabled", "provider": s.llm_provider,
                "sends_address_data": bool(svc and svc.llm_enabled)},
        "stt": {"status": "enabled" if svc and svc.stt else "disabled", "provider": s.stt_provider},
        "external_geocoders": {"status": "enabled" if svc and svc.external_geocoders else "disabled",
                               "providers": svc.external_geocoders if svc else []},
    }
    if g and db_ok:
        status, code = "ok", 200
    elif g:
        status, code = "degraded", 200  # can still resolve from memory; logs/feedback unavailable
    else:
        status, code = "unavailable", 503
    return JSONResponse(status_code=code, content={"status": status, "version": API_VERSION,
                                                   "environment": s.environment, "dependencies": deps})


@router.get("/v1/coverage", summary="What geographic data is loaded (and what is not)")
async def coverage() -> dict:
    g = get_gazetteer()
    if g is None:
        return {"success": False, "loaded": False}
    cov = g.coverage()
    has_osm = bool(cov["entities_by_type"].get("landmark") or cov["entities_by_type"].get("street")
                   or cov["entities_by_type"].get("osm_place"))
    return {
        "success": True,
        "loaded": True,
        **cov,
        "limitations": [
            "Localities come from India Post office names: every PIN code and post office in all 36 states/UTs, "
            "but neighbourhoods that are not post office names (e.g. many apartment-era layouts) are only "
            "matched if an alias or OpenStreetMap data exists.",
            "Coordinates are post-office or PIN-level approximations, not rooftop geocodes.",
            "Street, cross road, house number, building and landmark are extracted and normalised but cannot be "
            "verified" + (" (OSM streets/landmarks are loaded)." if has_osm else
                          " (no street/landmark dataset loaded; import OpenStreetMap with scripts/ingest.py --osm)."),
            "City names are curated for ~150 major cities; elsewhere the district is reported instead.",
        ],
    }


@router.get("/metrics", summary="Basic usage metrics (per process)")
async def get_metrics(client_id: str = Depends(authenticate)) -> dict:
    return metrics.snapshot()
