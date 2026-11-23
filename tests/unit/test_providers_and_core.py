"""LLM providers (mocked HTTP), STT providers, geocoders, rate limiting, auth helpers, logging redaction."""

import json

import httpx
import pytest

from app.core.config import Settings
from app.core.logging import redact, text_fingerprint
from app.core.ratelimit import TokenBucketLimiter
from app.core.security import key_id
from app.geography.providers.base import (
    GeoapifyProvider,
    NominatimProvider,
    PhotonProvider,
    build_external_providers,
)
from app.llm.client import LLMAddressExtractor, build_extractor
from app.llm.providers.base import LLMError, parse_json_object
from app.llm.providers.http_providers import AnthropicProvider, GoogleProvider, MockProvider, OpenAIProvider
from app.stt.base import DeepgramProvider, MockSTTProvider, build_stt

# ---------------------------------------------------------------- LLM


def test_parse_json_object_tolerates_fences():
    assert parse_json_object('```json\n{"city": "Pune"}\n```') == {"city": "Pune"}
    with pytest.raises(LLMError):
        parse_json_object("no json here")


def test_llm_extractor_validates_and_guards_pin():
    # The model hallucinates a PIN that is not in the input: it must be dropped.
    prov = MockProvider({"locality": "Koramangala", "city": "Bangalore", "pincode": "560034", "unknown_key": 1})
    out = LLMAddressExtractor(prov).extract("kormangla bangalore")
    assert out["locality"] == "Koramangala" and out["pincode"] is None
    out = LLMAddressExtractor(prov).extract("kormangla bangalore 560034")
    assert out["pincode"] == "560034"


def test_llm_requires_explicit_privacy_optin():
    assert build_extractor(Settings(llm_provider="mock", llm_send_address_data=False)) is None
    assert build_extractor(Settings(llm_provider="mock", llm_send_address_data=True)) is not None
    assert build_extractor(Settings(llm_provider="openai", llm_send_address_data=True, llm_api_key="")) is None


def _transport(payload: dict, status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def test_openai_anthropic_google_request_and_parse():
    seen: list = []
    p = OpenAIProvider("k", "m", transport=_transport(
        {"choices": [{"message": {"content": json.dumps({"city": "Pune"})}}]}, seen=seen))
    assert p.complete_json("s", "u") == {"city": "Pune"}
    assert seen[0].headers["authorization"] == "Bearer k"
    a = AnthropicProvider("k", transport=_transport({"content": [{"type": "text", "text": '{"state": "Goa"}'}]}))
    assert a.complete_json("s", "u") == {"state": "Goa"}
    g = GoogleProvider("k", transport=_transport({"candidates": [{"content": {"parts": [{"text": '{"x": 1}'}]}}]}))
    assert g.complete_json("s", "u") == {"x": 1}
    bad = OpenAIProvider("k", transport=_transport({}, status=500))
    with pytest.raises(LLMError):
        bad.complete_json("s", "u")


# ---------------------------------------------------------------- STT


async def test_mock_and_deepgram_stt():
    tr = await MockSTTProvider().transcribe(b"hsr layout bangalore")
    assert tr.text == "hsr layout bangalore"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Token dg"
        return httpx.Response(200, json={"results": {"channels": [{"detected_language": "en", "alternatives": [
            {"transcript": "14 main hsr", "confidence": 0.9}]}]}})

    dg = DeepgramProvider("dg", transport=httpx.MockTransport(handler))
    tr = await dg.transcribe(b"\x00\x01", "audio/wav")
    assert tr.text == "14 main hsr" and tr.confidence == 0.9
    assert build_stt("none", "") is None and build_stt("deepgram", "") is None


# ---------------------------------------------------------------- Geocoders


def test_geocoders_parse_and_filter_country():
    nom = NominatimProvider("http://nominatim.local", "test-agent", transport=_transport(
        [{"display_name": "HSR Layout, Bengaluru", "lat": "12.91", "lon": "77.64", "type": "suburb",
          "address": {"state": "Karnataka", "postcode": "560102", "city": "Bengaluru"}}]))
    r = nom.search("hsr layout")
    assert r[0].postcode == "560102" and r[0].latitude == 12.91
    photon = PhotonProvider("http://photon.local", transport=_transport({"features": [
        {"geometry": {"coordinates": [77.6, 12.9]}, "properties": {"name": "X", "countrycode": "IN"}},
        {"geometry": {"coordinates": [0, 0]}, "properties": {"name": "Y", "countrycode": "GB"}}]}))
    assert [x.name for x in photon.search("x")] == ["X"]
    geo = GeoapifyProvider("k", transport=_transport({"results": [{"formatted": "A", "lat": 1.0, "lon": 2.0}]}))
    assert geo.search("a")[0].name == "A"


def test_external_geocoders_need_privacy_optin():
    assert build_external_providers(Settings(geo_providers="local,photon", geo_send_address_data=False)) == []
    assert len(build_external_providers(Settings(geo_providers="local,photon", geo_send_address_data=True))) == 1


# ---------------------------------------------------------------- core


def test_token_bucket_limits_and_refills():
    lim = TokenBucketLimiter(rate_per_minute=60, burst=2)
    assert lim.try_acquire("c")[0] and lim.try_acquire("c")[0]
    ok, retry = lim.try_acquire("c")
    assert not ok and retry > 0
    assert lim.try_acquire("other")[0]  # independent per client


def test_redaction_and_key_ids_do_not_leak_secrets():
    r = redact("12 MG Road Bengaluru")
    assert "MG Road" not in r and text_fingerprint("12 MG Road Bengaluru") in r
    assert redact("x", allow=True) == "x"
    assert "secret" not in key_id("secret-key")


def test_settings_database_url_normalisation():
    assert Settings(database_url="postgres://u:p@h/db").database_url.startswith("postgresql+psycopg://")
    assert Settings(api_keys="a, b,,").api_key_list == ["a", "b"]
    assert Settings(environment="production").auth_required
    assert not Settings(environment="development").auth_required
