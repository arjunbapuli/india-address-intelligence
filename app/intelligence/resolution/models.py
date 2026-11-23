"""Domain models for a resolution result (also used directly as API response models)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ResolutionStatus = Literal["exact_match", "resolved", "partially_resolved", "ambiguous", "unresolved"]
Verification = Literal["verified", "corrected", "inferred", "unverified", "conflict"]


class AddressComponents(BaseModel):
    house_number: str | None = Field(None, description="House / door number as given. Never invented.")
    unit: str | None = Field(None, description="Flat / apartment / shop unit as given.")
    floor: str | None = None
    building: str | None = Field(None, description="Building, apartment complex or society name as given.")
    plot: str | None = None
    street: str | None = Field(None, description="Main road / street (e.g. '14th Main', 'ITPL Main Road').")
    cross_road: str | None = None
    lane: str | None = Field(None, description="Lane / gali (e.g. 'Gali No. 4').")
    block: str | None = None
    sector: str | None = None
    phase: str | None = None
    stage: str | None = None
    pocket: str | None = None
    locality: str | None = Field(None, description="Locality / neighbourhood / village matched in the gazetteer.")
    sub_locality: str | None = None
    landmark: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    postal_code: str | None = Field(None, description="6-digit PIN. Only populated when validated or derived "
                                                       "from an unambiguous locality (see verification).")
    country: str = "India"


class Correction(BaseModel):
    original: str
    corrected: str
    type: str = Field(..., description="formatting | filler_removal | abbreviation | number_normalization | "
                                       "transliteration | transcription_fix | spelling_correction | "
                                       "entity_expansion | inference")
    reason: str
    stage: str = "normalization"


class Candidate(BaseModel):
    normalized_address: str
    locality: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    postal_code: str | None = None
    score: float
    match_type: str | None = None
    matched_text: str | None = None


class Geolocation(BaseModel):
    latitude: float | None = None
    longitude: float | None = None
    accuracy: Literal["post_office_area", "pincode_centroid", "district_centroid", "city_centroid",
                      "landmark", "external_geocoder"] | None = None
    accuracy_note: str | None = None
    source: str | None = None


class ClarificationQuestion(BaseModel):
    field: str
    question: str
    options: list[str] = Field(default_factory=list)


class RankingEvidence(BaseModel):
    features: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    score: float = 0.0
    confidence_reasons: list[str] = Field(default_factory=list)


class Metadata(BaseModel):
    resolver_version: str
    data_sources: list[str] = Field(default_factory=list)
    languages_detected: list[str] = Field(default_factory=list)
    llm_used: bool = False
    external_geocoder_used: bool = False
    processing_time_ms: float = 0.0
    resolution_id: str | None = None


class Resolution(BaseModel):
    success: bool = True
    input_address: str
    normalized_transcript: str = Field(..., description="Input after Stage 1 normalisation (lower-case, tokens).")
    normalized_address: str | None
    components: AddressComponents
    component_verification: dict[str, Verification] = Field(
        default_factory=dict,
        description="Per-component provenance: verified against the gazetteer, corrected, inferred from "
                    "other evidence, unverified (kept as given), or in conflict.",
    )
    confidence: float = Field(..., ge=0, le=1)
    resolution_status: ResolutionStatus
    resolution_level: Literal["locality", "pincode", "city", "district", "state", "none"]
    needs_confirmation: bool
    needs_clarification: bool
    missing_components: list[str] = Field(default_factory=list)
    clarification_questions: list[ClarificationQuestion] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)
    alternatives: list[Candidate] = Field(default_factory=list)
    unparsed_text: list[str] = Field(default_factory=list,
                                     description="Parts of the input that could not be classified; preserved.")
    warnings: list[str] = Field(default_factory=list)
    geolocation: Geolocation = Field(default_factory=Geolocation)
    evidence: RankingEvidence = Field(default_factory=RankingEvidence)
    metadata: Metadata
