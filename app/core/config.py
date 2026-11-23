"""Environment-based configuration.

Every setting can be overridden with an environment variable of the same name
(case-insensitive). See `.env.example` for documentation of each variable.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Service -------------------------------------------------------------
    app_name: str = "India Address Intelligence API"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    docs_enabled: bool = True

    # --- Database ------------------------------------------------------------
    # Postgres in Docker/production; SQLite works for local experiments and tests.
    database_url: str = "sqlite:///./address_intel.db"
    # Seed the geographic database automatically on startup if it is empty.
    auto_seed: bool = True
    # Load the in-memory gazetteer on startup. Disable only for tooling.
    load_gazetteer_on_startup: bool = True

    # --- Auth & limits ---------------------------------------------------------
    # Comma-separated list of API keys. If empty and environment != production,
    # auth is disabled (handy locally). In production an empty list means every
    # authenticated route returns 401 until keys are configured.
    api_keys: str = ""
    auth_enabled: bool | None = None  # None = auto (on when keys configured or in production)
    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 30
    max_batch_size: int = 500
    max_address_length: int = 600

    # --- Resolution thresholds (evidence-based confidence) -------------------
    confidence_high: float = 0.85
    confidence_medium: float = 0.60
    confidence_low: float = 0.35
    # If the best two candidates in *different places* are closer than this,
    # the result is marked ambiguous.
    ambiguity_margin: float = 0.06
    max_alternatives: int = 5

    # Ranking weights (transparent weighted scoring, see docs/architecture.md)
    w_name_similarity: float = 0.30
    w_match_type: float = 0.10
    w_pin_consistency: float = 0.20
    w_city_consistency: float = 0.15
    w_state_consistency: float = 0.10
    w_landmark: float = 0.05
    w_proximity: float = 0.05
    w_source_confidence: float = 0.05

    # --- Privacy -------------------------------------------------------------
    # Store a log row per resolution (status, confidence, latency, input hash).
    store_resolution_metadata: bool = True
    # Store raw input text and resolved address. Off by default: addresses are PII.
    store_address_text: bool = False
    # Days to keep resolution logs / feedback raw text. 0 = keep forever.
    data_retention_days: int = 30
    # Log full addresses in application logs (never enable in production).
    log_address_text: bool = False

    # --- LLM (optional, off by default) ---------------------------------------
    llm_provider: Literal["none", "openai", "anthropic", "google", "openai_compatible", "mock"] = "none"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""  # for openai_compatible (vLLM, Ollama, Together, Groq, ...)
    llm_timeout_seconds: float = 8.0
    # "fallback": only call the LLM when deterministic extraction finds no place.
    # "always": call it on every request (slower, costs money).
    llm_mode: Literal["off", "fallback", "always"] = "fallback"
    # Explicit opt-in: sending addresses (PII) to a third-party LLM.
    llm_send_address_data: bool = False

    # --- External geocoders (optional, off by default) ------------------------
    geo_providers: str = "local"  # comma-separated: local,nominatim,photon,geoapify
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "india-address-intelligence/0.1 (set-your-contact-email)"
    photon_url: str = "https://photon.komoot.io"
    geoapify_api_key: str = ""
    external_geocoder_timeout_seconds: float = 4.0
    # Explicit opt-in: sending addresses to third-party geocoders.
    geo_send_address_data: bool = False

    # --- Speech-to-text (V2, optional) ----------------------------------------
    stt_provider: Literal["none", "deepgram", "openai_whisper", "sarvam", "mock"] = "none"
    stt_api_key: str = ""
    stt_model: str = ""
    max_audio_bytes: int = 10 * 1024 * 1024

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Render/Heroku style URLs -> SQLAlchemy psycopg3 driver
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

    @property
    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    @property
    def auth_required(self) -> bool:
        if self.auth_enabled is not None:
            return self.auth_enabled
        return bool(self.api_key_list) or self.environment == "production"

    @property
    def geo_provider_list(self) -> list[str]:
        return [p.strip().lower() for p in self.geo_providers.split(",") if p.strip()]

    @property
    def ranking_weights(self) -> dict[str, float]:
        return {
            "name_similarity": self.w_name_similarity,
            "match_type": self.w_match_type,
            "pin_consistency": self.w_pin_consistency,
            "city_consistency": self.w_city_consistency,
            "state_consistency": self.w_state_consistency,
            "landmark": self.w_landmark,
            "proximity": self.w_proximity,
            "source_confidence": self.w_source_confidence,
        }


settings_override: Settings | None = None


@lru_cache
def _cached_settings() -> Settings:
    return Settings()


def get_settings() -> Settings:
    if settings_override is not None:
        return settings_override
    return _cached_settings()
