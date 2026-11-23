# Architecture

## Overview

```text
                ┌────────────── FastAPI (app/main.py) ───────────────┐
 HTTP request → │ request-id · auth (X-API-Key) · rate limit · metrics │
                └───────────────────────┬─────────────────────────────┘
                                        │
     /audio ─► STT provider (optional) ─┤ transcript
                                        ▼
               ┌──────────── AddressResolver (app/intelligence/resolution) ────────────┐
               │ 1 normalize     form only: case, punctuation, numbers, ordinals,      │
               │                 abbreviations, Hinglish markers, transliteration,     │
               │                 STT homophones in numeric slots ("sector to" → 2)     │
               │ 2 extract       house / unit / floor / building / street / cross /    │
               │                 lane / sector / block / phase / landmark / PIN         │
               │ 3+5 match       n-grams → gazetteer: exact > alias > phonetic > fuzzy  │
               │                 fuzzy scoped to the city/district/PIN the user named  │
               │ 4 admin context city / district / state / PIN evidence + request hints │
               │ 6 rank          transparent weighted features (config weights)         │
               │ 7 assemble      components, verification labels, formatting           │
               │ 8 confidence    evidence-based score, status, questions to ask         │
               │   (optional) LLM proposes strings → gazetteer must confirm them       │
               │   (optional) external geocoder refines coordinates, bounded to area   │
               └─────────────────────────────┬──────────────────────────────────────────┘
                                             │ reads
               ┌─────────────────────────────▼──────────────────────────────────────────┐
               │ In-memory Gazetteer (app/geography/gazetteer.py), ~340 MB               │
               │ exact/alias index · Indic phonetic index · fuzzy key list ·            │
               │ PIN → localities · district → localities · city → districts · geo grid │
               └─────────────────────────────▲──────────────────────────────────────────┘
                                             │ loaded at startup
               ┌─────────────────────────────┴──────────────────────────────────────────┐
               │ PostgreSQL (system of record): geo_entities, geo_aliases, data_sources,│
               │ ingestion_runs, resolution_logs, feedback                              │
               └─────────────────────────────▲──────────────────────────────────────────┘
                                             │ scripts/ingest.py
               India Post directory · curated JSON · OpenStreetMap extract (optional)
```

## Why these choices

**Deterministic core, optional LLM.** Geography is a lookup problem: an LLM can't know whether a post office called "Velacheri" exists in Chennai, but a gazetteer can. The LLM is limited to proposing which words are the locality/city/landmark when deterministic extraction finds nothing (`LLM_MODE=fallback`). Every proposed name is looked up in the gazetteer, and a proposed PIN is dropped unless it literally appears in the input.

**In-memory gazetteer, database as system of record.** Resolution needs thousands of lookups per request (n-grams × exact/phonetic/fuzzy). Doing that in Postgres would mean many round-trips per call. Loading 186k entities into compact Python structures takes ~5 s at startup and makes a resolution take 1-5 ms. The database keeps provenance, licensing, aliases, logs and feedback, and new data sources only need an ingestion module.

**Postgres without PostGIS (for now).** Proximity queries are simple (nearest post office, landmarks within 3 km) and run on an in-memory grid. PostGIS becomes worth it when polygons (PIN boundaries, ward boundaries) are imported. The schema stores lat/lon so migrating is straightforward.

**Transparent weighted ranking, not ML.** Weights are in config (`W_*` env vars) and returned in every response (`evidence`). With ~100 labelled cases there isn't enough data to train a ranker. Once feedback produces a few thousand reviewed examples, a learned ranker (e.g. logistic regression on the same features) is a drop-in replacement for `ranking/ranker.py:score`.

**Scoped fuzzy matching.** A global fuzzy search over 150k names finds a near match for almost any word. Once the user names a city, district or PIN, fuzzy and phonetic matching is restricted to localities inside it, which is much more precise. Without that scope, approximate matches need longer keys and higher similarity, and common English/Hinglish words are never approximately matched.

## Pipeline details

### Stage 1: normalisation (`intelligence/normalization/`)
- Unicode NFKC; Indic digits (२५, ೨೫) → ASCII.
- Indic scripts → Latin via `indic-transliteration` (IAST), then simplified to how Indians type (schwa deletion for Hindi/Bengali/Gujarati/Punjabi/Odia; ṃ → n/m; loanwords like सेक्टर → sector).
- Phrase rules (`vocabulary.py`): fillers ("mera address hai"), Hinglish postpositions ("X ke paas" → landmark before marker), house/flat/plot/gali number markers, "mein" as separator.
- Abbreviations: rd, nr, opp, apt, blk, sec, ph, extn, ngr, h.no, x (cross)...
- Numbers: English words, compounds ("twenty first"), Hindi words only next to a numeric slot (so "Bara Bazar" isn't turned into "12 Bazar"), spoken digit strings with "double"/"triple" for PINs, ordinals before main/cross/stage/block/floor.
- STT homophones (to/too/for/won/ate → digits) only directly after sector/phase/block/stage/gali/floor/no. These are recorded as `transcription_fix` and always set `needs_confirmation`.
- Uncertainty phrases ("somewhere in", "shayad") are removed but remembered; they reduce confidence.

### Stage 2: extraction (`intelligence/extraction/rules.py`)
Multi-word gazetteer names are claimed first ("Koramangala 6th Block", "Banjara Hills") so slot rules don't break them apart. Then: PIN, house/flat/plot/shop numbers, floors, Nth main/cross/stage/block/phase, sector N, block X, road no N, named streets ("ITPL Main Road") and buildings ("Prestige Shantiniketan Apartments"), landmarks ("near X", "X ke paas"), and rural markers ("village X", "tehsil Y", "district Z").

### Stages 3-5: matching (`intelligence/matching/matcher.py`)
For each free span, n-grams (longest first): exact name → alias (old names, acronyms, transliterations, curated, feedback) → Indic phonetic key (`phonetics/indic.py`) → fuzzy (rapidfuzz). The phonetic key collapses aspirates (dh/d), vowel length, v/w/b, s/sh, doubled consonants and suffix spellings (-halli/-hally, -pur/-pura), so "Kormangla" = "Koramangala" and "Bengaluru" = "Bangalore".

### Stage 6: ranking (`intelligence/ranking/ranker.py`)
Features in [0, 1]: name_similarity, match_type, pin_consistency (PIN serves this locality or a same-name family like "Koramangala 6th Block"), city_consistency, state_consistency, landmark (OSM landmark within 3 km), proximity (request lat/lon), source_confidence. Missing evidence takes a neutral 0.6 (PIN/city/state) or is left out (landmark/proximity).

### Stage 7: assembly and formatting
Components get verification labels. PIN is filled only when given (verified or conflict) or derived from an unambiguous India Post locality (inferred, recorded as a correction). A locality that contradicts the city the user named is never returned; the result falls back to city level and the match goes into `alternatives`.

### Stage 8: confidence (`intelligence/confidence/engine.py`)
Start from the top score; cap by level (locality 0.99, PIN 0.88, city 0.72, district 0.66, state 0.42); +0.06 if no competitor in another place; ambiguity (competitor within `AMBIGUITY_MARGIN`) → ×0.8 and status `ambiguous`; penalties for STT fixes, fuzzy corrections, uncertainty, PIN conflicts. `exact_match` requires exact names, a consistent PIN and no corrections.

## Extending

- **New data source**: write a reader that returns `EntityRecord`/`AliasRecord` (see `geography/ingestion/osm.py`), register it in `SOURCES` in `pipeline.py`, and add a CLI flag in `scripts/ingest.py`.
- **New LLM**: subclass `LLMProvider` (`llm/providers/base.py`), one method `complete_json`.
- **New STT**: subclass `SpeechToTextProvider` (`stt/base.py`), one method `transcribe`.
- **New geocoder**: subclass `GeoProvider` (`geography/providers/base.py`).
- **New language**: add phrase rules to `vocabulary.py`, loanwords to `transliterate.py`, and Indic-script aliases to curated JSON (they are indexed via transliteration automatically).
