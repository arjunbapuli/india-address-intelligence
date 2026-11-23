"""Relational schema (SQLAlchemy 2.0). See docs/schema.md for the rationale.

Design choice: one `geo_entities` table for every kind of place (state, district,
city, pincode, locality, landmark, street, ...) with denormalised hierarchy
columns (state/district/pincode). This keeps the prototype schema small while
still supporting hierarchy checks, aliases and provenance. Split into
per-type tables only when a type needs columns the others don't.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# BIGINT on Postgres, INTEGER on SQLite (so SQLite autoincrement works)
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class DataSource(Base):
    """Provenance + licensing for every record we ingest."""

    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    license: Mapped[str] = mapped_column(String(255))
    attribution: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(512))
    version: Mapped[str | None] = mapped_column(String(64))
    default_confidence: Mapped[float] = mapped_column(Float, default=0.8)
    retrieved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="running")
    records_in: Mapped[int] = mapped_column(Integer, default=0)
    records_written: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class GeoEntity(Base):
    """Any geographic entity: state, district, city, pincode, locality, landmark, street..."""

    __tablename__ = "geo_entities"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("geo_entities.id"), nullable=True)
    state_name: Mapped[str | None] = mapped_column(String(128))
    district_name: Mapped[str | None] = mapped_column(String(128))
    city_name: Mapped[str | None] = mapped_column(String(128))
    pincode: Mapped[str | None] = mapped_column(String(6))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    source_record_id: Mapped[str | None] = mapped_column(String(128))
    source_confidence: Mapped[float] = mapped_column(Float, default=0.8)
    attributes: Mapped[dict | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                    onupdate=func.now())

    aliases: Mapped[list[GeoAlias]] = relationship(back_populates="entity", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_geo_entities_type_norm", "entity_type", "normalized_name"),
        Index("ix_geo_entities_pincode", "pincode"),
        Index("ix_geo_entities_district", "state_name", "district_name"),
        Index("ix_geo_entities_source_record", "source_id", "source_record_id"),
        Index("ix_geo_entities_parent", "parent_id"),
    )


class GeoAlias(Base):
    """Alternate names: abbreviations, old names, transliterations, misspellings, acronyms."""

    __tablename__ = "geo_aliases"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("geo_entities.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(255))
    normalized_alias: Mapped[str] = mapped_column(String(255), index=True)
    alias_type: Mapped[str] = mapped_column(String(32))  # alternate_name|old_name|abbreviation|acronym|transliteration|misspelling|feedback
    language: Mapped[str | None] = mapped_column(String(16))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id"))
    confidence: Mapped[float] = mapped_column(Float, default=0.9)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    entity: Mapped[GeoEntity] = relationship(back_populates="aliases")


class ResolutionLog(Base):
    """One row per resolution. By default stores no address text (PII) — only a
    fingerprint, outcome and timing. Raw text is stored only when
    STORE_ADDRESS_TEXT=true and is purged after DATA_RETENTION_DAYS."""

    __tablename__ = "resolution_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    client_id: Mapped[str | None] = mapped_column(String(64))
    endpoint: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(32))
    input_length: Mapped[int] = mapped_column(Integer)
    language_hint: Mapped[str | None] = mapped_column(String(32))
    input_text: Mapped[str | None] = mapped_column(Text)
    normalized_address: Mapped[str | None] = mapped_column(Text)
    components: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    needs_confirmation: Mapped[bool] = mapped_column(Boolean)
    resolved_pincode: Mapped[str | None] = mapped_column(String(6))
    resolved_district: Mapped[str | None] = mapped_column(String(128))
    resolved_state: Mapped[str | None] = mapped_column(String(128))
    latency_ms: Mapped[float] = mapped_column(Float)
    resolver_version: Mapped[str] = mapped_column(String(16))


class Feedback(Base):
    """Customer corrections. Never treated as ground truth automatically:
    rows start as `pending_review` and pass quality checks before they can be
    promoted into aliases / evaluation data (see scripts/review_feedback.py)."""

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    resolution_id: Mapped[str | None] = mapped_column(ForeignKey("resolution_logs.id", ondelete="SET NULL"),
                                                      nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(64))
    accepted: Mapped[bool | None] = mapped_column(Boolean)
    correction_was_wrong: Mapped[bool | None] = mapped_column(Boolean)
    audio_reference: Mapped[str | None] = mapped_column(String(512))
    original_transcript: Mapped[str | None] = mapped_column(Text)
    resolved_address: Mapped[str | None] = mapped_column(Text)
    corrected_address: Mapped[str | None] = mapped_column(Text)
    corrected_components: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(32), default="pending_review")
    quality_flags: Mapped[dict | None] = mapped_column(JSON)
