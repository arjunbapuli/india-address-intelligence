"""Persistence for resolution logs, feedback and data retention."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import delete, insert, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import text_fingerprint
from app.database.models import Feedback, ResolutionLog
from app.geography.gazetteer import Gazetteer
from app.intelligence.resolution.models import AddressComponents, Resolution
from app.intelligence.text import compact_key
from app.version import RESOLVER_VERSION


def new_resolution_id() -> str:
    return str(uuid.uuid4())


def resolution_log_row(res: Resolution, settings: Settings, client_id: str | None, endpoint: str,
                       language_hint: str | None, latency_ms: float) -> dict:
    """Build a log row. Address text is only included when STORE_ADDRESS_TEXT=true."""
    keep_text = settings.store_address_text
    return {
        "id": res.metadata.resolution_id or new_resolution_id(),
        "client_id": client_id,
        "endpoint": endpoint,
        "input_fingerprint": text_fingerprint(res.input_address),
        "input_length": len(res.input_address),
        "language_hint": language_hint,
        "input_text": res.input_address if keep_text else None,
        "normalized_address": res.normalized_address if keep_text else None,
        "components": res.components.model_dump() if keep_text else None,
        "status": res.resolution_status,
        "confidence": res.confidence,
        "needs_confirmation": res.needs_confirmation,
        # Coarse geography is kept even without text: useful for coverage analytics, not identifying.
        "resolved_pincode": res.components.postal_code,
        "resolved_district": res.components.district,
        "resolved_state": res.components.state,
        "latency_ms": latency_ms,
        "resolver_version": RESOLVER_VERSION,
    }


def write_resolution_logs(session: Session, rows: list[dict]) -> None:
    if rows:
        session.execute(insert(ResolutionLog), rows)


def feedback_quality_flags(g: Gazetteer | None, comp: AddressComponents | None, corrected_address: str | None,
                           resolved_address: str | None, resolution_found: bool | None) -> dict[str, bool | str]:
    """Automatic checks run before a human reviews feedback. Feedback is never auto-promoted to truth."""
    flags: dict[str, bool | str] = {}
    flags["has_correction"] = bool(corrected_address or comp)
    if resolution_found is not None:
        flags["resolution_id_found"] = resolution_found
    if corrected_address and resolved_address:
        flags["correction_differs_from_resolution"] = compact_key(corrected_address) != compact_key(resolved_address)
    if comp is not None and g is not None:
        if comp.postal_code:
            flags["corrected_pincode_exists"] = comp.postal_code in g.pin_place
        if comp.locality:
            hits = [g.places[h.place_id] for h in g.lookup_exact(comp.locality)]
            flags["corrected_locality_known"] = bool(hits)
            if comp.postal_code and hits:
                flags["corrected_locality_matches_pincode"] = any(p.pincode == comp.postal_code for p in hits)
        if comp.state:
            flags["corrected_state_known"] = any(g.places[h.place_id].type == "state" for h in g.lookup_exact(comp.state))
    return flags


def create_feedback(session: Session, data: dict, flags: dict, client_id: str | None, settings: Settings) -> Feedback:
    keep_text = settings.store_address_text
    fb = Feedback(
        id=str(uuid.uuid4()),
        resolution_id=data.get("resolution_id"),
        client_id=client_id,
        accepted=data.get("accepted"),
        correction_was_wrong=data.get("correction_was_wrong"),
        audio_reference=data.get("audio_reference"),
        # Feedback text IS the point of feedback, but it is still PII: it is kept only
        # for DATA_RETENTION_DAYS and is cleared by the retention job.
        original_transcript=data.get("original_transcript"),
        resolved_address=data.get("resolved_address"),
        corrected_address=data.get("corrected_address"),
        corrected_components=data.get("corrected_components"),
        notes=data.get("notes"),
        review_status="pending_review",
        quality_flags={**flags, "address_text_retention": "standard" if keep_text else "feedback_only"},
    )
    session.add(fb)
    return fb


def purge_expired(session: Session, retention_days: int) -> dict[str, int]:
    """Delete resolution logs and scrub feedback text older than the retention window."""
    if retention_days <= 0:
        return {"resolution_logs_deleted": 0, "feedback_scrubbed": 0}
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=retention_days)
    session.execute(update(Feedback).where(Feedback.created_at < cutoff, Feedback.resolution_id.is_not(None))
                    .values(resolution_id=None))
    r1 = session.execute(delete(ResolutionLog).where(ResolutionLog.created_at < cutoff))
    r2 = session.execute(
        update(Feedback).where(Feedback.created_at < cutoff, Feedback.original_transcript.is_not(None)).values(
            original_transcript=None, resolved_address=None, corrected_address=None, corrected_components=None,
            notes=None))
    return {"resolution_logs_deleted": r1.rowcount or 0, "feedback_scrubbed": r2.rowcount or 0}
