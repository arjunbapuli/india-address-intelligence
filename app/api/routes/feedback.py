"""Feedback loop: customers report whether a resolution was accepted or how it was corrected."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import Services, get_services
from app.api.schemas.requests import FeedbackRequest, FeedbackResponse
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.ratelimit import enforce_rate_limit
from app.core.security import authenticate
from app.database.models import ResolutionLog
from app.database.repositories.records import create_feedback, feedback_quality_flags
from app.database.session import session_scope

router = APIRouter(prefix="/v1", tags=["feedback"])


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    summary="Submit feedback / correction for a resolution",
    description=(
        "Stores the outcome of a resolution (accepted, corrected, or wrong correction). Every submission is "
        "stored as `pending_review` with automatic quality flags; nothing is promoted to aliases or "
        "evaluation data without review (see docs/feedback.md). Text is purged after DATA_RETENTION_DAYS."
    ),
)
async def submit_feedback(req: FeedbackRequest, client_id: str = Depends(authenticate),
                          svc: Services = Depends(get_services)) -> FeedbackResponse:
    enforce_rate_limit(client_id)
    settings = get_settings()
    try:
        with session_scope() as s:
            found = None
            if req.resolution_id:
                found = s.scalar(select(ResolutionLog.id).where(ResolutionLog.id == req.resolution_id)) is not None
            flags = feedback_quality_flags(svc.gazetteer, req.corrected_components, req.corrected_address,
                                           req.resolved_address, found)
            data = req.model_dump()
            if req.corrected_components is not None:
                data["corrected_components"] = req.corrected_components.model_dump()
            if found is False:
                data["resolution_id"] = None  # keep the FK valid; the flag records the mismatch
            fb = create_feedback(s, data, flags, client_id, settings)
            fid, status = fb.id, fb.review_status
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(503, "storage_unavailable", "Feedback could not be stored right now.") from exc
    return FeedbackResponse(feedback_id=fid, review_status=status, quality_flags=flags)
