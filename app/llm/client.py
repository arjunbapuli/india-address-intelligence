"""LLM-assisted address extraction (optional).

The LLM is used for what it is good at — reading messy, code-switched speech
and proposing which words are the locality / city / landmark — and nothing
else. Its output is schema-validated and then treated as *untrusted text*: the
resolver looks every proposed name up in the gazetteer, and an LLM proposal
can never create a place, a PIN code or a house number.

Privacy: the extractor is only built when LLM_PROVIDER is set AND
LLM_SEND_ADDRESS_DATA=true, because the address is sent to a third party.
"""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.logging import get_logger
from app.llm.providers.base import LLMError, LLMProvider
from app.llm.providers.http_providers import (
    AnthropicProvider,
    GoogleProvider,
    MockProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
)

log = get_logger(__name__)

SYSTEM_PROMPT = """You read Indian delivery addresses, often from noisy speech-to-text transcripts in English,
Hindi, Hinglish or other Indian languages, and return their parts as JSON.

Return exactly these keys (use null when a part is not present in the text):
house_number, unit, floor, building, street, cross_road, sector, block, phase, locality, sub_locality,
landmark, city, district, state, pincode.

Rules:
- Copy values from the text, correcting only obvious speech-to-text spelling errors of place names.
- Never invent a PIN code, house number, street, landmark or locality that is not in the text.
- Do not guess a city or state that was not said.
- Hinglish: "ke paas" = near (landmark), "mein" = in, "gali" = lane, "ghar/makan number" = house number.
- Output JSON only."""

FEW_SHOT = """Example
Text: "mera ghar 42 hai, teesra cross, indra nagar ke paas metro station, bangalore"
JSON: {"house_number":"42","unit":null,"floor":null,"building":null,"street":null,"cross_road":"3rd Cross",
"sector":null,"block":null,"phase":null,"locality":"Indiranagar","sub_locality":null,"landmark":"Metro Station",
"city":"Bangalore","district":null,"state":null,"pincode":null}
"""


class LLMExtraction(BaseModel):
    house_number: str | None = None
    unit: str | None = None
    floor: str | None = None
    building: str | None = None
    street: str | None = None
    cross_road: str | None = None
    sector: str | None = None
    block: str | None = None
    phase: str | None = None
    locality: str | None = None
    sub_locality: str | None = None
    landmark: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = None


class LLMAddressExtractor:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def extract(self, text: str) -> dict:
        user = f"{FEW_SHOT}\nText: {text!r}\nJSON:"
        raw = self.provider.complete_json(SYSTEM_PROMPT, user)
        try:
            data = LLMExtraction.model_validate({k: (str(v) if v is not None else None) for k, v in raw.items()
                                                 if k in LLMExtraction.model_fields})
        except ValidationError as exc:
            raise LLMError("LLM output failed schema validation") from exc
        out = data.model_dump()
        # Deterministic guard: a PIN must be 6 digits and must literally appear in the input
        pin = out.get("pincode")
        if pin and (not pin.isdigit() or len(pin) != 6 or pin not in text.replace(" ", "")):
            out["pincode"] = None
        return out


def build_provider(s: Settings) -> LLMProvider | None:
    p = s.llm_provider
    if p == "none":
        return None
    if p == "mock":
        return MockProvider()
    if not s.llm_api_key and p != "openai_compatible":
        log.warning("llm_provider_configured_without_key", extra={"extra_fields": {"provider": p}})
        return None
    if p == "openai":
        return OpenAIProvider(s.llm_api_key, s.llm_model, timeout=s.llm_timeout_seconds)
    if p == "anthropic":
        return AnthropicProvider(s.llm_api_key, s.llm_model, timeout=s.llm_timeout_seconds)
    if p == "google":
        return GoogleProvider(s.llm_api_key, s.llm_model, timeout=s.llm_timeout_seconds)
    if p == "openai_compatible":
        return OpenAICompatibleProvider(s.llm_api_key, s.llm_model, s.llm_base_url, timeout=s.llm_timeout_seconds)
    return None


def build_extractor(s: Settings) -> LLMAddressExtractor | None:
    if s.llm_provider == "none" or s.llm_mode == "off":
        return None
    if not s.llm_send_address_data:
        log.warning("llm_disabled_privacy_optin_missing",
                    extra={"extra_fields": {"hint": "set LLM_SEND_ADDRESS_DATA=true to allow sending addresses"}})
        return None
    provider = build_provider(s)
    return LLMAddressExtractor(provider) if provider else None
