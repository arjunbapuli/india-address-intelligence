# API reference

Base URL locally: `http://localhost:8000`. Interactive docs: `/docs` (Swagger) and `/redoc`. OpenAPI JSON: `/openapi.json`.

**Auth:** send `X-API-Key: <key>` (or `Authorization: Bearer <key>`). Keys come from `API_KEYS`. In development with no keys configured, auth is off; in production it is always on.

**Errors** always look like:

```json
{"success": false, "error": {"code": "rate_limited", "message": "...", "details": {...}}, "request_id": "..."}
```

Codes: `missing_api_key`, `invalid_api_key` (401), `validation_error` (422, never echoes the address), `batch_too_large` (422), `pincode_not_found` (404), `rate_limited` (429 with `Retry-After`), `gazetteer_not_loaded` (503), `stt_not_configured` (501), `internal_error` (500).

Every response carries `X-Request-ID` (send your own to correlate).

## Endpoints, latency and limits

Latency is server time on a 2-vCPU machine with the full India Post gazetteer loaded, LLM disabled.

| Endpoint | Purpose | Typical latency | Limits |
|---|---|---|---|
| `POST /v1/address/resolve` | Resolve one address/transcript | 1-5 ms (p95 < 20 ms) | `address_text` ≤ 600 chars; 1 rate-limit token |
| `POST /v1/address/resolve/batch` | Resolve many | ~0.8 ms/address (100 ≈ 80 ms) | ≤ 500 items (`MAX_BATCH_SIZE`); cost 1 + n/10 tokens |
| `POST /v1/address/validate` | Validate text or components | < 20 ms | as resolve |
| `GET /v1/address/search` | Autocomplete / candidates | 10-40 ms | `q` ≤ 100 chars, `limit` ≤ 50 |
| `GET /v1/pincode/{pin}` | PIN → post offices, district, state | < 5 ms | |
| `POST /v1/feedback` | Report acceptance / corrections | < 20 ms (DB write) | |
| `POST /v1/address/resolve/audio` | Audio → STT → resolve | STT time (0.5-3 s) + ~5 ms | ≤ 10 MB (`MAX_AUDIO_BYTES`); 5 tokens; 501 if no STT |
| `GET /v1/coverage` | What data is loaded, limitations | < 5 ms | no auth |
| `GET /health` | Dependencies; 503 if gazetteer missing | < 5 ms | no auth |
| `GET /metrics` | Per-process usage counters | < 5 ms | auth |
| `GET /` | API info | < 5 ms | no auth |

Default rate limit: 120 requests/minute per key, burst 30 (`RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_BURST`).

## POST /v1/address/resolve

Request:

```json
{
  "address_text": "14 main hsr sector to near nifty bangalore",
  "country": "IN",
  "city": "Bengaluru",
  "state": null,
  "pincode": null,
  "language": "hinglish",
  "latitude": null,
  "longitude": null
}
```

Only `address_text` is required. `city`, `state`, `pincode` and `latitude`/`longitude` are hints (for example from your CRM or the device): they narrow the search but never override what the customer said.

Response (abridged; the full example is in the README):

```json
{
  "success": true,
  "input_address": "14 main hsr sector to near nifty bangalore",
  "normalized_transcript": "14th main hsr sector 2 near nifty bangalore",
  "normalized_address": "14th Main, Sector 2, Near Nifty, HSR Layout, Bengaluru, Karnataka 560102, India",
  "components": {"street": "14th Main", "sector": "Sector 2", "locality": "HSR Layout", "landmark": "Nifty",
                 "city": "Bengaluru", "district": "Bengaluru Urban", "state": "Karnataka",
                 "postal_code": "560102", "country": "India", "house_number": null, "...": null},
  "component_verification": {"sector": "corrected", "street": "unverified", "locality": "verified",
                             "city": "verified", "district": "inferred", "state": "inferred",
                             "postal_code": "inferred", "landmark": "unverified"},
  "confidence": 0.88,
  "resolution_status": "resolved",
  "resolution_level": "locality",
  "needs_confirmation": true,
  "needs_clarification": true,
  "missing_components": ["house_number"],
  "clarification_questions": [{"field": "house_number", "question": "Could you share the house, flat or building number?", "options": []}],
  "corrections": [
    {"original": "sector to", "corrected": "sector 2", "type": "transcription_fix", "reason": "'to' after 'sector' is a common speech-to-text error for 2", "stage": "normalization"},
    {"original": "hsr", "corrected": "HSR Layout", "type": "entity_expansion", "reason": "known alternate name (acronym)", "stage": "matching"},
    {"original": "", "corrected": "560102", "type": "inference", "reason": "PIN of the only post office named HSR Layout in Bengaluru Urban", "stage": "resolution"}
  ],
  "alternatives": [],
  "unparsed_text": [],
  "warnings": [],
  "geolocation": {"latitude": 12.91411, "longitude": 77.637152, "accuracy": "post_office_area", "source": "india_post"},
  "evidence": {"features": {"name_similarity": 0.85, "match_type": 0.95, "pin_consistency": 0.6, "city_consistency": 1.0, "state_consistency": 1.0, "source_confidence": 0.9},
               "weights": {"...": 0.3}, "score": 0.85, "confidence_reasons": ["no competing candidate in another place", "1 speech-to-text correction(s) applied"]},
  "metadata": {"resolver_version": "0.1.0", "data_sources": ["india_post"], "languages_detected": ["en"], "llm_used": false, "processing_time_ms": 4.8, "resolution_id": "31379e82-..."}
}
```

### Statuses

| Status | Meaning | Suggested action |
|---|---|---|
| `exact_match` | Exact place names, PIN agrees, nothing corrected | Use as is |
| `resolved` | Locality-level, confidence ≥ `CONFIDENCE_HIGH` | Use; read back if `needs_confirmation` |
| `partially_resolved` | Only city/district/PIN level, or locality with medium confidence | Confirm with the customer |
| `ambiguous` | Two or more places fit almost equally | Ask the `clarification_questions` |
| `unresolved` | Nothing geographic recognised | Ask for area and city |

### Correction types

`formatting`, `filler_removal`, `abbreviation`, `number_normalization`, `transliteration` (form only) · `transcription_fix` (STT homophone) · `spelling_correction` (fuzzy/phonetic match to a known place) · `entity_expansion` (alias → canonical: Bangalore → Bengaluru, HSR → HSR Layout) · `inference` (derived value such as a PIN).

## POST /v1/address/resolve/batch

```json
{
  "addresses": [
    {"id": "order-1", "address_text": "flat 302 sobha apartments bellandur bangalore"},
    {"id": "order-2", "address_text": "andheri west mumbai 400053"},
    {"id": "order-3", "address_text": "  "}
  ],
  "defaults": {"city": "Bengaluru"}
}
```

Response: `count`, `succeeded`, `failed`, `processing_time_ms`, and `results[]` with `index`, `id`, `success`, `result` (same as `/resolve`) or `error`. Invalid items fail individually (`order-3` gets `validation_error`); the batch still returns 200.

## POST /v1/address/validate

Either `address_text` (resolved first, then validated) or `components` (validated exactly as given):

```json
{"components": {"house_number": "12", "locality": "Koramangala", "city": "Bengaluru", "state": "Tamil Nadu", "postal_code": "560034"}}
```

Response: `is_valid`, `structurally_valid`, `pincode_valid`, `pincode_matches_locality`, `pincode_matches_state`, `city_state_plausible`, `geolocation_resolvable`, `missing_components`, `needs_clarification`, and `checks[]` with pass/fail/warn and a message each.

## GET /v1/address/search

`?q=kormangla&type=locality&state=Karnataka&limit=5`

Supports prefixes (`Kora`), misspellings (`kormangla`), phonetic variants, aliases (`Bombay`, `Gurgaon`), PIN prefixes (`5601`) and a trailing city that narrows results (`anna nagar chennai`). Each hit has `type`, `name`, `match_type`, `score`, `label`, hierarchy fields, PIN and coordinates.

## POST /v1/feedback

```json
{
  "resolution_id": "31379e82-...",
  "original_transcript": "14 main hsr sector to near nifty bangalore",
  "accepted": false,
  "correction_was_wrong": false,
  "corrected_components": {"locality": "HSR Layout", "sector": "Sector 2", "postal_code": "560102", "state": "Karnataka"},
  "notes": "customer confirmed sector 2"
}
```

Response: `feedback_id`, `review_status: "pending_review"`, and automatic `quality_flags` (does the corrected PIN exist? is the locality known? do they agree?). See [feedback.md](feedback.md).

## POST /v1/address/resolve/audio

`multipart/form-data` with `audio` (file), optional `language` (e.g. `en-IN`, `hi`), `city`, `state`. Returns `transcript`, `stt_provider`, `stt_confidence` and `resolution`. Needs `STT_PROVIDER` and `STT_API_KEY`. Audio is not stored.

```bash
curl -s localhost:8000/v1/address/resolve/audio -H 'X-API-Key: local-dev-key' \
  -F audio=@address.wav -F language=en-IN
```
