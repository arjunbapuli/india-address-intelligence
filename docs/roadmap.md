# Roadmap

## V1: Address intelligence API (this repository)

Done: text/transcript input, normalisation (numbers, ordinals, abbreviations, Hinglish, Indic-script transliteration, STT homophones), component extraction, India-wide gazetteer from India Post (all PINs and post offices, 36 states/UTs), alias and phonetic and fuzzy matching, weighted ranking, evidence-based confidence and statuses, clarification questions, validation, search, batch, feedback capture, privacy defaults, Docker, tests, evaluation tooling.

Next in V1:
- Import OpenStreetMap India; re-run the coverage-gap evaluation.
- Exact multi-word matches for generic names inside a named city ("Park Street, Kolkata").
- Redis rate limiting and Prometheus metrics for multi-instance deployments.
- CI (GitHub Actions): ruff, pytest, evaluation diff per pull request.

## V2: Voice address intelligence

- Production STT integration (Deepgram / Sarvam / IndicConformer), using STT n-best alternatives as extra candidates.
- Streaming: resolve partial transcripts so the agent can confirm the locality while the customer is still speaking.
- Dialogue helpers: confirmation prompts and spelling-mode capture ("H for Hyderabad").
- Language-specific number words and ordinals for Kannada, Tamil, Telugu, Marathi, Bengali.

## V3: Indian address specialisation

- LGD village/town codes and Census directories for rural coverage.
- PIN boundary polygons (PostGIS) for point-in-polygon validation.
- Better phonetics: script-aware keys (Tamil voicing, Bengali b/v), learned from feedback pairs.
- Regional-language address vocabulary (ಹತ್ತಿರ, அருகில், దగ్గర for "near").
- Learned ranker on the existing features once there are a few thousand reviewed outcomes.

## V4: Learning system

- Feedback review UI, alias promotion workflow, automated regression evaluation per resolver version.
- Proprietary address dataset from consented, reviewed interactions.
- Fine-tuned small extraction model (on-prem friendly) if it beats rules + LLM fallback on real data.

## V5: Commercial API

- API key management with per-customer quotas, usage-based billing, customer dashboard.
- SDKs (Python, Node), webhooks, enterprise integrations (Shopify, Unicommerce, Shiprocket, CRMs, voice platforms).
- SLAs, multi-region deployment, data-residency options.
