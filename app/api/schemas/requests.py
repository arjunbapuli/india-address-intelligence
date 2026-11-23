"""Request / response schemas for the public API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.geography.search.service import SearchHit
from app.intelligence.resolution.models import AddressComponents, Resolution

PIN_PATTERN = r"^[1-9][0-9]{5}$"


class ResolveRequest(BaseModel):
    address_text: str = Field(..., min_length=1, max_length=600,
                              description="Raw address text or STT transcript (any mix of English, Hindi, Hinglish "
                                          "or Indic scripts).",
                              examples=["14 main hsr sector to near nifty bangalore"])
    country: Literal["IN"] = Field("IN", description="Only India ('IN') is supported.")
    city: str | None = Field(None, max_length=80, description="Optional hint, e.g. the customer's city from CRM.")
    state: str | None = Field(None, max_length=80, description="Optional hint.")
    pincode: str | None = Field(None, description="Optional 6-digit PIN hint.")
    language: str | None = Field(None, max_length=32, description="Optional hint: en, hi, hinglish, kn, ta ...")
    latitude: float | None = Field(None, ge=6.0, le=37.6, description="Optional hint (e.g. device location).")
    longitude: float | None = Field(None, ge=68.0, le=97.6)
    id: str | None = Field(None, max_length=128, description="Optional client reference echoed back in batches.")

    @field_validator("pincode")
    @classmethod
    def _pin(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v = v.replace(" ", "")
        import re

        if not re.fullmatch(PIN_PATTERN, v):
            raise ValueError("pincode must be 6 digits and not start with 0")
        return v

    @field_validator("address_text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("address_text must not be blank")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [{"address_text": "14 main hsr sector to near nifty bangalore", "country": "IN",
                          "city": "Bengaluru", "language": "hinglish"}]
        }
    }


class BatchResolveRequest(BaseModel):
    # Items are validated one by one inside the handler so a single bad item can't fail the batch.
    addresses: list[dict[str, Any]] = Field(
        ..., min_length=1, description="Up to MAX_BATCH_SIZE (default 500). Each item has the same fields as "
                                       "a /v1/address/resolve request (address_text, city, state, pincode, id ...).",
        examples=[[{"id": "order-1", "address_text": "flat 302 sobha apartments bellandur bangalore"},
                   {"id": "order-2", "address_text": "andheri west mumbai 400053"}]])
    defaults: dict[str, str] | None = Field(None, description="Hints applied to items that don't set them: "
                                                              "city, state, language.")


class ItemError(BaseModel):
    code: str
    message: str


class BatchItemResult(BaseModel):
    index: int
    id: str | None = None
    success: bool
    result: Resolution | None = None
    error: ItemError | None = None


class BatchResolveResponse(BaseModel):
    success: bool = True
    count: int
    succeeded: int
    failed: int
    processing_time_ms: float
    results: list[BatchItemResult]


class ValidateRequest(BaseModel):
    address_text: str | None = Field(None, max_length=600)
    components: AddressComponents | None = Field(None, description="Structured address to validate as given.")
    country: Literal["IN"] = "IN"
    city: str | None = None
    state: str | None = None
    pincode: str | None = None

    @model_validator(mode="after")
    def _one_of(self) -> ValidateRequest:
        if not (self.address_text and self.address_text.strip()) and self.components is None:
            raise ValueError("provide address_text or components")
        return self


class SearchResponse(BaseModel):
    success: bool = True
    query: str
    count: int
    results: list[SearchHit]


class FeedbackRequest(BaseModel):
    resolution_id: str | None = Field(None, description="metadata.resolution_id from a previous resolve call.")
    original_transcript: str | None = Field(None, max_length=1000)
    audio_reference: str | None = Field(None, max_length=512, description="Your own pointer to the audio (URL/ID); "
                                                                          "audio is never uploaded here.")
    resolved_address: str | None = Field(None, max_length=1000)
    corrected_address: str | None = Field(None, max_length=1000)
    corrected_components: AddressComponents | None = None
    accepted: bool | None = Field(None, description="Did the customer/agent accept the resolution as is?")
    correction_was_wrong: bool | None = Field(None, description="Did our automatic correction make it worse?")
    notes: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _something(self) -> FeedbackRequest:
        if self.accepted is None and not self.corrected_address and self.corrected_components is None \
                and self.correction_was_wrong is None:
            raise ValueError("feedback must include accepted, correction_was_wrong or a correction")
        return self


class FeedbackResponse(BaseModel):
    success: bool = True
    feedback_id: str
    review_status: str
    quality_flags: dict[str, bool | str]
