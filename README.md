# India Address Intelligence API

Turns noisy, spoken, misspelled or code-switched Indian addresses into structured, geographically validated addresses, with a confidence score you can act on.

```text
"14 main hsr sector to near nifty bangalore"          (speech-to-text output)
        │
        ▼
14th Main, Sector 2, Near Nifty, HSR Layout, Bengaluru, Karnataka 560102, India
  locality  HSR Layout   verified   (India Post)
  sector    Sector 2     corrected  ("sector to" is an STT error for "sector 2")
  street    14th Main    unverified (no open street data for this area)
  PIN       560102       inferred   (only post office named HSR Layout in Bengaluru Urban)
  status    resolved, confidence 0.88, needs_confirmation = true, ask for house number
```

It is built for voice agents, e-commerce checkout, logistics and contact centres: it can take a transcript, return what it understood, say what it corrected and why, and tell the agent what to ask next ("Which Rajajinagar do you mean: Bengaluru or Chennai?").

## What works today

| Capability | Status |
|---|---|
| Resolve text / STT transcripts (`POST /v1/address/resolve`) | ✅ |
| Batch resolve, up to 500 per call, per-item errors (`/v1/address/resolve/batch`) | ✅ |
| Validate an address or structured components (`/v1/address/validate`) | ✅ |
| Autocomplete / candidate search (`GET /v1/address/search`) | ✅ |
| PIN lookup (`GET /v1/pincode/{pin}`), coverage report (`GET /v1/coverage`) | ✅ |
| Feedback with quality flags and human review (`POST /v1/feedback`) | ✅ |
| Audio input via pluggable STT (Deepgram, Whisper, Sarvam) (`/v1/address/resolve/audio`) | ✅ code + mocked tests; needs your STT key |
| Optional LLM assist (OpenAI, Anthropic, Google, any OpenAI-compatible model) | ✅ code + mocked tests; off by default |
| Optional external geocoders (Nominatim, Photon, Geoapify) | ✅ code + mocked tests; off by default |
| OpenStreetMap import for streets, landmarks, neighbourhoods | ✅ tested on a fixture; run it where you have internet |
| API keys, rate limiting, request IDs, structured logs, metrics, Swagger | ✅ |
| Docker / Compose / Render / Fly configs | ✅ (entrypoint verified natively; see Known limitations) |

## Quick start

```bash
git clone https://github.com/arjunbapuli/india-address-intelligence
cd india-address-intelligence
docker compose up
```

The first start migrates the database and loads India Post data (about 30 seconds). Then:

- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- Default local API key: `local-dev-key` (set `API_KEYS` in `.env` to change it)

Without Docker (Python 3.12+, SQLite):

```bash
pip install -e ".[dev]"
DATABASE_URL=sqlite:///./address_intel.db uvicorn app.main:app --reload
```

No external API key is needed. LLM, STT and external geocoders are optional and the API runs fully without them.

## Try it

```bash
curl -s localhost:8000/v1/address/resolve \
  -H 'X-API-Key: local-dev-key' -H 'content-type: application/json' \
  -d '{"address_text": "14 main hsr sector to near nifty bangalore", "country": "IN", "language": "hinglish"}'

curl -s 'localhost:8000/v1/address/search?q=kormangla&limit=3' -H 'X-API-Key: local-dev-key'

curl -s localhost:8000/v1/address/validate -H 'X-API-Key: local-dev-key' -H 'content-type: application/json' \
  -d '{"components": {"house_number": "12", "locality": "Koramangala", "city": "Bengaluru", "state": "Tamil Nadu", "postal_code": "560034"}}'
```

More examples, including batch, feedback and audio, are in [docs/api.md](docs/api.md).

### Reading a response

| Field | Meaning |
|---|---|
| `normalized_address` | Canonical address. Never contains invented house numbers, streets or PINs. |
| `components` | House, unit, floor, building, street, cross road, lane, block, sector, phase, locality, landmark, city, district, state, PIN. |
| `component_verification` | Per field: `verified` (matches data), `corrected` (we changed it), `inferred` (derived, e.g. state from locality), `unverified` (kept as given), `conflict` (e.g. PIN belongs to another state). |
| `confidence` | 0-1, computed from evidence (string similarity, PIN/city/state agreement, competing candidates, corrections), never from an LLM's opinion. |
| `resolution_status` | `exact_match`, `resolved` (safe to use), `partially_resolved`, `ambiguous`, `unresolved`. |
| `needs_confirmation` / `needs_clarification` | Read back to the customer / ask for more. |
| `clarification_questions` | Ready-to-speak questions for a voice agent. |
| `corrections` | Every change, with type and reason. |
| `alternatives` | Other candidates with scores. |
| `evidence` | Ranking features and weights behind the score. |

## Geographic data

| Source | What it gives | Licence |
|---|---|---|
| India Post All-India Pincode Directory (via the pinned `indiapins==1.1.0` package, data updated 21 Feb 2026) | 165,515 post-office localities, 19,586 PINs, 754 districts, all 36 states and UTs, coordinates for ~148k offices | GODL-India (attribution) |
| Curated naming data in `datasets/curated/` | State abbreviations and old names, renamed districts (Gurgaon → Gurugram), 147 cities mapped to districts, colloquial locality names (Salt Lake → Bidhan Nagar) | MIT (this repo) |
| OpenStreetMap, optional import | Streets, landmarks, neighbourhoods that aren't post-office names | ODbL, © OpenStreetMap contributors |

**Coverage, stated plainly:** every PIN code and every post office in India is covered. Neighbourhoods that are not post-office names (BTM Layout, Koregaon Park, Indirapuram, Hazratganj...) are only found after an OpenStreetMap import or a curated or feedback alias. Without that, the API falls back to city level and says so. Streets and house numbers are extracted and normalised but cannot be verified without OSM data. Details: [docs/data-sources.md](docs/data-sources.md).

## Evaluation (actual results)

Deterministic pipeline only (no LLM). Full per-case output is in `evaluation/results/`.

| Dataset | N | Locality | State | Exact match | Auto-resolved | Precision when auto-resolved | Wrong place-name corrections | Calibration error (ECE) |
|---|---|---|---|---|---|---|---|---|
| Held-out test, **first run** | 108 | 73.5% | 99.0% | 73.2% | 52.8% | **100%** | 41.7% (5/12) | 0.158 |
| Held-out test, after bug fixes | 108 | 83.7% | 100% | 84.3% | 59.3% | **100%** | 15.4% (2/13) | 0.100 |
| ...excluding 14 known coverage-gap cases | 94 | 94.2% | 100% | 93.6% | 68.1% | 100% | 0% (0/11) | 0.090 |
| Synthetic STT noise (generated from the data, see caveat) | 500 | 96.6% | 98.2% | 96.4% | 79.0% | 99.5% | 1.0% | 0.066 |

Caveats, so these numbers aren't over-read:

- I (an AI) wrote both the rules and the 108 test cases. The test set was written before tuning, and the first-run numbers are reported. The fixes after that run were general bugs found by reading the failures (listed in [docs/evaluation.md](docs/evaluation.md)), so the "after fixes" row is optimistic.
- 14 test cases are deliberately well-known places that India Post does not list. They show the real coverage gap: 1 of 12 resolved correctly at locality level. All 14 got the right state and city.
- The synthetic set measures robustness to spelling noise on names that exist in the data. It says nothing about coverage.
- Real customer transcripts are the next step. See the roadmap.

## Tests

```bash
pytest            # 103 tests: unit + integration on a real SQLite DB seeded with full India Post data (~30 s)
ruff check app scripts tests evaluation
python -m evaluation.run_eval --all
```

## Deployment

Recommended low-cost setup: **Render** (Docker web service + managed Postgres) with the included `render.yaml` blueprint. Starter instance (512 MB) about $7/month, plus Postgres about $6/month. The API uses ~340 MB of RAM with the full India Post data loaded. A Fly.io config (Mumbai region) is included too. See [docs/deployment.md](docs/deployment.md).

## Documentation

- [Architecture and pipeline](docs/architecture.md)
- [API reference and examples](docs/api.md) (plus live Swagger at `/docs`)
- [Data sources, licensing and ingestion](docs/data-sources.md)
- [Database schema](docs/schema.md)
- [Evaluation methodology and results](docs/evaluation.md)
- [Privacy and security](docs/privacy.md)
- [Feedback loop](docs/feedback.md)
- [Deployment](docs/deployment.md)
- [Roadmap](docs/roadmap.md)

## Known limitations

1. **Locality coverage = post-office names** unless OSM is imported. Many modern neighbourhoods fall back to city level.
2. **Streets, cross roads, house numbers and buildings are not verified.** They are extracted, normalised and returned as `unverified`.
3. **Coordinates are approximate**: post office (typically 1-5 km from the address), PIN centroid or district centre. ~5,900 implausible post-office coordinates were discarded at ingestion.
4. **City names** are curated for 147 cities. Elsewhere the district is reported, and the city is only auto-filled for fully urban districts (Bengaluru Urban, Mumbai, Delhi, Chennai, Kolkata, Hyderabad, Chandigarh).
5. **Languages**: English, Hindi (both scripts) and Hinglish are handled directly. Kannada, Tamil, Telugu, Malayalam, Bengali, Gujarati, Punjabi and Odia scripts are transliterated for matching, which works for many place names but is not full language understanding. Tamil script loses voicing (ட = t/d), which hurts some matches.
6. **Generic multi-word names** (e.g. "Park Street") are never fuzzy-matched, to avoid false matches. Exact matches inside the named city are a planned improvement.
7. **Rate limiting is in-process**, so it is per instance. Use Redis if you run several instances.
8. **Docker images weren't built in the development sandbox** (Docker Hub was unreachable). The entrypoint (migrate, seed, serve) was run natively against an empty Postgres and verified end to end.
9. **Type checking** runs (`mypy app`) but is advisory. About 30 Optional-typing warnings remain.

## Next three highest-impact improvements

1. **Import OpenStreetMap for India** (`python -m scripts.ingest --osm india-latest.osm.pbf`) and measure the coverage-gap set again. This adds neighbourhoods, streets and landmarks.
2. **Evaluate on real, consented call transcripts** from a voice deployment (a few hundred with confirmed addresses), then tune the ranking weights and thresholds on that rather than on synthetic or AI-written cases.
3. **Close the loop on feedback**: review queue → promote aliases → re-run evaluation, so colloquial names customers actually use flow into the gazetteer.

## Licence

Code: MIT. Data: see [docs/data-sources.md](docs/data-sources.md). India Post data under GODL-India requires attribution. OSM data, if imported, is ODbL.
