# Database schema

Migrations: Alembic (`app/database/migrations/`, `alembic upgrade head`). Models: `app/database/models.py`. Works on PostgreSQL (production) and SQLite (local/tests).

```text
data_sources 1──* geo_entities 1──* geo_aliases
     │                 │ (parent_id → geo_entities, optional)
     └──* ingestion_runs

resolution_logs 1──* feedback (resolution_id, nullable)
```

## Tables

### `geo_entities`
One table for every kind of place. `entity_type` ∈ `state`, `district`, `city`, `pincode`, `locality`, `osm_place`, `landmark`, `street`.

| Column | Notes |
|---|---|
| `id` | bigint PK |
| `entity_type`, `name`, `normalized_name` | `normalized_name` = lowercase alphanumerics, no spaces |
| `state_name`, `district_name`, `city_name`, `pincode` | Denormalised hierarchy (fast consistency checks without joins) |
| `parent_id` | Optional explicit parent (indexed) |
| `latitude`, `longitude` | WGS84; null when unknown or implausible |
| `source_id`, `source_record_id` | Provenance and the source's natural key (e.g. `po:560102:hsr layout`, `osm:n123`) |
| `source_confidence` | Prior reliability (0-1) used in ranking |
| `attributes` | JSON: office type, delivery status, division, coordinate quality, city districts, OSM tags |
| `is_active`, `created_at`, `updated_at` | Freshness and soft-disable |

Indexes: (`entity_type`, `normalized_name`), `pincode`, (`state_name`, `district_name`), (`source_id`, `source_record_id`), `parent_id`.

### `geo_aliases`
Alternate names: `alias`, `normalized_alias`, `alias_type` (`alternate_name`, `old_name`, `abbreviation`, `acronym`, `transliteration`, `misspelling`, `feedback`), `language`, `source_id`, `confidence`. Indic-script aliases are matched through their transliteration.

### `data_sources` and `ingestion_runs`
Licence, attribution, URL, version, retrieval time and record counts per source; one row per ingestion run with status and counts.

### `resolution_logs`
One row per resolution: `client_id` (hashed key id), `endpoint`, `input_fingerprint` (SHA-256 prefix), `input_length`, `status`, `confidence`, `needs_confirmation`, resolved PIN/district/state, `latency_ms`, `resolver_version`. **`input_text`, `normalized_address` and `components` are null unless `STORE_ADDRESS_TEXT=true`.** Rows are deleted after `DATA_RETENTION_DAYS`.

### `feedback`
`resolution_id`, `accepted`, `correction_was_wrong`, `original_transcript`, `audio_reference` (your pointer; audio isn't uploaded), `resolved_address`, `corrected_address`, `corrected_components`, `notes`, `review_status` (`pending_review` / `approved` / `rejected`), `quality_flags` (JSON). Text fields are scrubbed after `DATA_RETENTION_DAYS`.

## Why this shape

- **One entity table instead of one per type**: every type needs the same things (name, hierarchy, coordinates, provenance, aliases). Splitting now would multiply joins and migrations for no benefit. If a type grows its own columns (e.g. building polygons), it gets a side table keyed by `geo_entities.id`.
- **Denormalised hierarchy**: India Post rows carry state/district names directly and resolution checks consistency constantly. `parent_id` exists for sources with real trees (OSM admin boundaries, LGD).
- **Provenance on every row** so licences can be honoured and sources replaced independently.
- **Logs separate from geography and free of PII by default**, so analytics don't require storing addresses.
