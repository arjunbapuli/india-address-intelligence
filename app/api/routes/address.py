"""Address endpoints: resolve, batch resolve, validate, search, PIN lookup."""

from __future__ import annotations

import time

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from app.api.deps import Services, get_services
from app.api.schemas.requests import (
    BatchItemResult,
    BatchResolveRequest,
    BatchResolveResponse,
    ItemError,
    ResolveRequest,
    SearchResponse,
    ValidateRequest,
)
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.logging import get_logger, log_event, redact
from app.core.metrics import metrics
from app.core.ratelimit import enforce_rate_limit
from app.core.security import authenticate
from app.database.repositories.records import new_resolution_id, resolution_log_row, write_resolution_logs
from app.database.session import session_scope
from app.geography.search.service import search as gazetteer_search
from app.geography.validation.validator import ValidationResult, validate_components
from app.intelligence.resolution.models import Resolution
from app.intelligence.resolution.resolver import Hints

router = APIRouter(prefix="/v1", tags=["address"])
log = get_logger(__name__)


def _hints(req: ResolveRequest, defaults: dict[str, str] | None = None) -> Hints:
    d = defaults or {}
    return Hints(city=req.city or d.get("city"), state=req.state or d.get("state"), pincode=req.pincode,
                 latitude=req.latitude, longitude=req.longitude, language=req.language or d.get("language"))


def _persist_logs(rows: list[dict]) -> None:
    try:
        with session_scope() as s:
            write_resolution_logs(s, rows)
    except Exception as exc:  # logging must never break the API
        log.warning("resolution_log_write_failed", extra={"extra_fields": {"error": type(exc).__name__}})


def _resolve_one(svc: Services, req: ResolveRequest, defaults: dict[str, str] | None = None) -> Resolution:
    settings = get_settings()
    if len(req.address_text) > settings.max_address_length:
        raise ApiError(422, "address_too_long", f"address_text exceeds {settings.max_address_length} characters")
    res = svc.resolver.resolve(req.address_text, _hints(req, defaults))
    res.metadata.resolution_id = new_resolution_id()
    metrics.record_resolution(res.resolution_status)
    return res


@router.post(
    "/address/resolve",
    response_model=Resolution,
    summary="Resolve a noisy address / STT transcript",
    description=(
        "Normalises, extracts, matches against the India-wide gazetteer, ranks candidates and returns a "
        "structured address with evidence-based confidence.\n\n**Typical latency:** 2-20 ms server time "
        "(+ LLM latency if an LLM is enabled). **Limits:** `address_text` ≤ 600 characters."
    ),
)
async def resolve(req: ResolveRequest, request: Request, background: BackgroundTasks,
                  client_id: str = Depends(authenticate), svc: Services = Depends(get_services)) -> Resolution:
    enforce_rate_limit(client_id)
    t0 = time.perf_counter()
    res = await run_in_threadpool(_resolve_one, svc, req)
    settings = get_settings()
    log_event(log, "address_resolved", status=res.resolution_status, confidence=res.confidence,
              resolution_level=res.resolution_level, input=redact(req.address_text, settings.log_address_text),
              resolution_id=res.metadata.resolution_id)
    if settings.store_resolution_metadata:
        background.add_task(_persist_logs, [resolution_log_row(res, settings, client_id, "resolve", req.language,
                                                               (time.perf_counter() - t0) * 1000)])
    return res


@router.post(
    "/address/resolve/batch",
    response_model=BatchResolveResponse,
    summary="Resolve many addresses in one call",
    description=(
        "Each item is resolved independently; a failing item returns `success=false` with an error and never "
        "fails the batch. Rate-limit cost: 1 token + 1 per 10 items.\n\n**Typical latency:** ~5 ms per address "
        "(100 addresses ≈ 0.5 s). **Limits:** up to MAX_BATCH_SIZE items (default 500)."
    ),
)
async def resolve_batch(req: BatchResolveRequest, background: BackgroundTasks,
                        client_id: str = Depends(authenticate), svc: Services = Depends(get_services)
                        ) -> BatchResolveResponse:
    settings = get_settings()
    if len(req.addresses) > settings.max_batch_size:
        raise ApiError(422, "batch_too_large", f"at most {settings.max_batch_size} addresses per batch")
    enforce_rate_limit(client_id, cost=1 + len(req.addresses) / 10)
    t0 = time.perf_counter()

    def work() -> list[BatchItemResult]:
        out: list[BatchItemResult] = []
        for i, raw in enumerate(req.addresses):
            ref = str(raw.get("id"))[:128] if isinstance(raw, dict) and raw.get("id") is not None else None
            try:
                item = ResolveRequest.model_validate(raw)
            except ValidationError as exc:
                msg = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
                out.append(BatchItemResult(index=i, id=ref, success=False,
                                           error=ItemError(code="validation_error", message=msg)))
                continue
            try:
                out.append(BatchItemResult(index=i, id=item.id, success=True,
                                           result=_resolve_one(svc, item, req.defaults)))
            except ApiError as exc:
                out.append(BatchItemResult(index=i, id=item.id, success=False,
                                           error=ItemError(code=exc.code, message=exc.message)))
            except Exception as exc:  # isolate failures per item
                log.exception("batch_item_failed", extra={"extra_fields": {"index": i, "error": type(exc).__name__}})
                out.append(BatchItemResult(index=i, id=item.id, success=False,
                                           error=ItemError(code="internal_error", message="could not resolve item")))
        return out

    results = await run_in_threadpool(work)
    elapsed = (time.perf_counter() - t0) * 1000
    if settings.store_resolution_metadata:
        rows = [resolution_log_row(r.result, settings, client_id, "resolve_batch", None, elapsed / max(len(results), 1))
                for r in results if r.result is not None]
        background.add_task(_persist_logs, rows)
    ok = sum(1 for r in results if r.success)
    return BatchResolveResponse(count=len(results), succeeded=ok, failed=len(results) - ok,
                                processing_time_ms=round(elapsed, 2), results=results)


@router.post(
    "/address/validate",
    response_model=ValidationResult,
    summary="Validate an address (text or structured components)",
    description=(
        "Checks structure, PIN existence, PIN↔locality and PIN↔state agreement, city/state plausibility and "
        "whether coordinates can be derived. With `address_text` the address is resolved first; with "
        "`components` the fields are validated exactly as given.\n\n**Typical latency:** < 20 ms."
    ),
)
async def validate(req: ValidateRequest, client_id: str = Depends(authenticate),
                   svc: Services = Depends(get_services)) -> ValidationResult:
    enforce_rate_limit(client_id)
    g = svc.gazetteer
    if req.components is not None:
        return validate_components(g, req.components)
    rr = ResolveRequest(address_text=req.address_text or "", city=req.city, state=req.state, pincode=req.pincode)
    res = await run_in_threadpool(_resolve_one, svc, rr)
    return validate_components(g, res.components, res)


@router.get(
    "/address/search",
    response_model=SearchResponse,
    summary="Autocomplete / candidate search",
    description=(
        "Ranked candidates for states, districts, cities, PIN codes and localities (and landmarks/streets when "
        "OpenStreetMap data has been imported). Supports prefixes ('Kora'), misspellings ('kormangla'), "
        "phonetic variants, aliases ('Bombay', 'Gurgaon') and PIN prefixes ('5600').\n\n**Typical latency:** "
        "10-40 ms."
    ),
)
async def search(q: str = Query(..., min_length=1, max_length=100, description="Search text or PIN prefix"),
                 type: list[str] | None = Query(None, description="Filter: state, district, city, pincode, "  # noqa: A002
                                                                    "locality, landmark, street"),
                 state: str | None = Query(None, max_length=80),
                 district: str | None = Query(None, max_length=80),
                 limit: int = Query(10, ge=1, le=50),
                 client_id: str = Depends(authenticate), svc: Services = Depends(get_services)) -> SearchResponse:
    enforce_rate_limit(client_id)
    hits = await run_in_threadpool(gazetteer_search, svc.gazetteer, q, type, state, district, limit)
    return SearchResponse(query=q, count=len(hits), results=hits)


@router.get(
    "/pincode/{pincode}",
    summary="Look up a PIN code",
    description="Post offices, district and state for a 6-digit PIN from the India Post directory.",
)
async def pincode(pincode: str = Path(..., pattern=r"^[1-9][0-9]{5}$"), client_id: str = Depends(authenticate),
                  svc: Services = Depends(get_services)) -> dict:
    enforce_rate_limit(client_id)
    g = svc.gazetteer
    pid = g.pin_place.get(pincode)
    if pid is None:
        raise ApiError(404, "pincode_not_found", "PIN code not found in the India Post directory.")
    p = g.places[pid]
    offices = [g.places[i] for i in g.pin_localities.get(pincode, [])]
    city = next((g.city_for_locality(o) for o in offices if g.city_for_locality(o)), None)
    return {
        "success": True, "pincode": pincode, "district": p.district, "state": p.state,
        "city": city.name if city else None,
        "latitude": p.lat, "longitude": p.lon, "coordinate_accuracy": "pincode_centroid" if p.lat else None,
        "post_offices": [{"name": o.name, "office_type": o.office_type, "delivery": o.delivery,
                          "district": o.district, "latitude": o.lat, "longitude": o.lon} for o in offices],
        "source": "india_post",
    }
