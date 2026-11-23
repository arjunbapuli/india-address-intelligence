# Feedback loop

Goal: learn from real outcomes (accepted, corrected, wrongly corrected) without letting unreviewed corrections poison the data.

## Flow

```text
POST /v1/feedback ──► feedback row (pending_review) + automatic quality_flags
                              │
               python -m scripts.review_feedback list
                              │  human decides
            approve ──────────┼────────── reject
               │
   optional: promote alias ──► geo_aliases (alias_type='feedback') ──► restart API ──► used in matching
               │
   export ──► reviewed.jsonl ──► evaluation set / ranking-weight tuning / future fine-tuning
```

## Automatic quality flags

Computed on submission (`app/database/repositories/records.py`):

- `resolution_id_found`: does the referenced resolution exist?
- `has_correction`, `correction_differs_from_resolution`
- `corrected_pincode_exists`: is the corrected PIN in India Post data?
- `corrected_locality_known`, `corrected_locality_matches_pincode`
- `corrected_state_known`

They prioritise review; they never auto-approve.

## Why nothing is automatic

Customer corrections are noisy: agents mis-key, customers give an office address, people use informal names for places a kilometre away. Promoting one directly could start making other customers' addresses wrong. The review step, plus requiring a promoted alias to point at an existing India Post locality (name + PIN), keeps the gazetteer trustworthy.

## Uses once volume exists

- **Evaluation:** reviewed items become a real-world test set (the most valuable next step; see evaluation.md).
- **Ranking:** tune `W_*` weights and thresholds against accepted/rejected outcomes; later, a learned ranker on the same features.
- **Alias discovery:** cluster corrections that map the same informal name to the same locality; review and promote.
- **Gazetteer enrichment:** corrected localities missing from the data point to coverage gaps to fill (OSM, LGD, or a new curated entry).
- **Fine-tuning:** exported pairs (transcript → corrected components) for an extraction model, if the LLM path proves worth it.
