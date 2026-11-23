"""HTTP API tests (FastAPI TestClient) against the seeded database."""

from app.core import config
from app.core.ratelimit import reset_limiter
from app.llm.client import LLMAddressExtractor
from app.llm.providers.http_providers import MockProvider
from app.stt.base import MockSTTProvider


def test_root_and_health(client):
    r = client.get("/")
    assert r.status_code == 200 and "resolve" in r.json()["capabilities"]
    h = client.get("/health").json()
    assert h["status"] == "ok"
    assert h["dependencies"]["gazetteer"]["entities"] > 180000
    assert h["dependencies"]["llm"]["sends_address_data"] is False


def test_auth_required_and_request_id(client):
    r = client.post("/v1/address/resolve", json={"address_text": "hsr layout"}, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    r = client.post("/v1/address/resolve", json={"address_text": "hsr layout"}, headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"


def test_resolve_endpoint_schema(client):
    r = client.post("/v1/address/resolve", json={"address_text": "14 main hsr sector to near nifty bangalore",
                                                 "country": "IN", "city": "Bengaluru", "language": "hinglish"})
    assert r.status_code == 200
    d = r.json()
    assert d["components"]["locality"] == "HSR Layout"
    assert d["resolution_status"] in ("resolved", "partially_resolved")
    assert 0 <= d["confidence"] <= 1
    assert d["metadata"]["resolution_id"]
    assert set(d["component_verification"].values()) <= {"verified", "corrected", "inferred", "unverified", "conflict"}


def test_validation_errors_do_not_echo_address(client):
    r = client.post("/v1/address/resolve", json={"address_text": "secret street 9", "pincode": "012345"})
    assert r.status_code == 422
    assert "secret street" not in r.text


def test_batch_partial_failures(client):
    items = [{"address_text": "koramangala 560034", "id": "a"}, {"address_text": "  ", "id": "b"},
             {"pincode": "1"}] + [{"address_text": f"sector {i} noida"} for i in range(1, 101)]
    r = client.post("/v1/address/resolve/batch", json={"addresses": items})
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 103 and d["failed"] == 2 and d["succeeded"] == 101
    assert d["results"][0]["id"] == "a" and d["results"][0]["result"]["components"]["postal_code"] == "560034"
    assert d["results"][1]["error"]["code"] == "validation_error"


def test_batch_size_limit(client):
    r = client.post("/v1/address/resolve/batch", json={"addresses": [{"address_text": "x"}] * 501})
    assert r.status_code == 422 and r.json()["error"]["code"] == "batch_too_large"


def test_validate_components_and_text(client):
    bad = client.post("/v1/address/validate", json={"components": {
        "house_number": "12", "locality": "Koramangala", "city": "Bengaluru", "state": "Tamil Nadu",
        "postal_code": "560034"}}).json()
    assert bad["is_valid"] is False and bad["pincode_matches_state"] is False and bad["city_state_plausible"] is False
    good = client.post("/v1/address/validate", json={
        "address_text": "flat 302, sobha apartments, bellandur, bangalore 560103"}).json()
    assert good["is_valid"] and good["pincode_valid"] and good["missing_components"] == []
    missing = client.post("/v1/address/validate", json={"components": {"city": "Pune", "state": "Maharashtra"}}).json()
    assert "house_number" in missing["missing_components"] and missing["needs_clarification"]


def test_search_endpoint(client):
    d = client.get("/v1/address/search", params={"q": "kormangla", "limit": 3}).json()
    assert d["results"][0]["name"] == "Koramangala"
    d = client.get("/v1/address/search", params={"q": "Bombay", "type": "city"}).json()
    assert d["results"][0]["name"] == "Mumbai"
    d = client.get("/v1/address/search", params={"q": "5601", "limit": 5}).json()
    assert all(x["type"] == "pincode" and x["name"].startswith("5601") for x in d["results"])
    d = client.get("/v1/address/search", params={"q": "anna nagar chennai"}).json()
    assert d["results"][0]["district"] == "Chennai"


def test_pincode_lookup(client):
    d = client.get("/v1/pincode/560102").json()
    assert d["state"] == "Karnataka" and any(o["name"] == "HSR Layout" for o in d["post_offices"])
    assert client.get("/v1/pincode/999999").status_code == 404


def test_feedback_flow(client):
    rid = client.post("/v1/address/resolve", json={"address_text": "hsr layout bangalore"}).json()["metadata"][
        "resolution_id"]
    r = client.post("/v1/feedback", json={"resolution_id": rid, "accepted": False, "corrected_components": {
        "locality": "HSR Layout", "postal_code": "560102", "state": "Karnataka"}})
    assert r.status_code == 200
    d = r.json()
    assert d["review_status"] == "pending_review"
    assert d["quality_flags"]["corrected_locality_matches_pincode"] is True
    r = client.post("/v1/feedback", json={"corrected_components": {"locality": "Nowhereville", "postal_code": "999999"}})
    flags = r.json()["quality_flags"]
    assert flags["corrected_pincode_exists"] is False and flags["corrected_locality_known"] is False
    assert client.post("/v1/feedback", json={"notes": "hi"}).status_code == 422


def test_audio_endpoint_with_mock_stt(client):
    assert client.post("/v1/address/resolve/audio", files={"audio": ("a.wav", b"x")}).status_code == 501
    svc = client.app.state.services
    svc.stt = MockSTTProvider()
    r = client.post("/v1/address/resolve/audio", files={"audio": ("a.wav", b"hsr layout bangalore", "audio/wav")})
    assert r.status_code == 200
    assert r.json()["resolution"]["components"]["locality"] == "HSR Layout"
    svc.stt = None


def test_llm_fallback_is_validated_against_gazetteer(client):
    """The (mock) LLM proposes a locality; the gazetteer must confirm it; invented values are ignored."""
    svc = client.app.state.services
    prev_llm, prev_settings = svc.resolver.llm, svc.resolver.s
    svc.resolver.llm = LLMAddressExtractor(MockProvider({"locality": "Koramangala", "city": "Bangalore",
                                                         "pincode": "560034"}))
    svc.resolver.s = prev_settings.model_copy(update={"llm_send_address_data": True, "llm_mode": "fallback"})
    try:
        d = client.post("/v1/address/resolve", json={"address_text": "uh the place with the big forum mall"}).json()
        assert d["metadata"]["llm_used"] is True
        assert d["components"]["locality"] == "Koramangala"
        assert d["components"]["postal_code"] != "560034" or d["component_verification"]["postal_code"] != "verified"
        # An LLM proposal that doesn't exist in the gazetteer produces nothing
        svc.resolver.llm = LLMAddressExtractor(MockProvider({"locality": "Zzyzx Nagar"}))
        d = client.post("/v1/address/resolve", json={"address_text": "something unclear"}).json()
        assert d["components"]["locality"] is None
    finally:
        svc.resolver.llm, svc.resolver.s = prev_llm, prev_settings


def test_rate_limit(client):
    prev = config.settings_override
    config.settings_override = prev.model_copy(update={"rate_limit_per_minute": 1, "rate_limit_burst": 2})
    reset_limiter()
    try:
        codes = [client.get("/v1/address/search", params={"q": "pune"}).status_code for _ in range(4)]
        assert codes[:2] == [200, 200] and codes[-1] == 429
        r = client.get("/v1/address/search", params={"q": "pune"})
        assert r.json()["error"]["code"] == "rate_limited" and "Retry-After" in r.headers
    finally:
        config.settings_override = prev
        reset_limiter()


def test_metrics_and_coverage(client):
    client.post("/v1/address/resolve", json={"address_text": "pune"})
    m = client.get("/metrics").json()
    assert m["addresses_processed"] >= 1
    cov = client.get("/v1/coverage").json()
    assert cov["states_and_uts"] == 36 and cov["pincodes"] > 19000
    assert "india_post" in cov["sources"] and cov["limitations"]
