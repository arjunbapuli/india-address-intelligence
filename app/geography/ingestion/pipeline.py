"""Modular ingestion pipeline: source readers -> entity/alias records -> database.

Add a new data source by writing a reader that yields `EntityRecord` and
`AliasRecord` objects and registering it in `SOURCES`. The core application
never changes: the gazetteer reloads whatever is in the database.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Sequence

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_event
from app.database.models import DataSource, GeoAlias, GeoEntity, IngestionRun
from app.geography.ingestion.curated import (
    city_entities,
    curated_aliases,
    load_curated,
    locality_alias_records,
)
from app.geography.ingestion.india_post import (
    AliasRecord,
    EntityRecord,
    build_entities,
    iter_chunks,
    read_indiapins_package,
    read_ogd_csv,
)
from app.intelligence.text import compact_key

log = get_logger(__name__)

SOURCES: dict[str, dict] = {
    "india_post": {
        "name": "India Post - All India Pincode Directory",
        "license": "Government Open Data License - India (GODL-India)",
        "attribution": (
            "Contains data from the Department of Posts, Government of India, published on the Open Government "
            "Data Platform India (data.gov.in). Packaged by the 'indiapins' project (MIT licence)."
        ),
        "url": "https://www.data.gov.in/catalog/all-india-pincode-directory",
        "default_confidence": 0.9,
    },
    "curated": {
        "name": "Project-curated naming data (states, districts, cities)",
        "license": "MIT",
        "attribution": "india-address-intelligence contributors (datasets/curated/*.json)",
        "url": None,
        "default_confidence": 0.95,
    },
    "osm": {
        "name": "OpenStreetMap",
        "license": "Open Database License (ODbL) 1.0",
        "attribution": "© OpenStreetMap contributors",
        "url": "https://www.openstreetmap.org/copyright",
        "default_confidence": 0.75,
    },
}


def ensure_source(session: Session, key: str, version: str | None = None) -> DataSource:
    meta = SOURCES[key]
    src = session.scalar(select(DataSource).where(DataSource.key == key))
    if src is None:
        src = DataSource(key=key, **meta)
        session.add(src)
    else:
        for k, v in meta.items():
            setattr(src, k, v)
    src.version = version or src.version
    src.retrieved_at = dt.datetime.now(dt.UTC)
    session.flush()
    return src


def write_records(
    session: Session, source: DataSource, entities: Sequence[EntityRecord], aliases: Sequence[AliasRecord],
    replace: bool = True,
) -> tuple[int, int]:
    """Insert entities and aliases for one source. With replace=True, existing rows of that source are removed."""
    if replace:
        ids = select(GeoEntity.id).where(GeoEntity.source_id == source.id)
        session.execute(delete(GeoAlias).where(GeoAlias.entity_id.in_(ids)))
        session.execute(delete(GeoEntity).where(GeoEntity.source_id == source.id))
        session.flush()
    for chunk in iter_chunks(entities, 5000):
        session.execute(
            insert(GeoEntity),
            [
                {
                    "entity_type": e.entity_type, "name": e.name, "normalized_name": compact_key(e.name),
                    "state_name": e.state, "district_name": e.district, "city_name": e.city, "pincode": e.pincode,
                    "latitude": e.latitude, "longitude": e.longitude, "source_id": source.id,
                    "source_record_id": e.key, "source_confidence": e.source_confidence,
                    "attributes": e.attributes, "is_active": True,
                }
                for e in chunk
            ],
        )
    session.flush()
    key_to_id: dict[str, int] = dict(
        session.execute(  # type: ignore[arg-type]
            select(GeoEntity.source_record_id, GeoEntity.id).where(GeoEntity.source_id == source.id)
        ).all()
    )
    return len(entities), _write_aliases(session, source, aliases, key_to_id)


def _write_aliases(session: Session, source: DataSource, aliases: Sequence[AliasRecord],
                   key_to_id: dict[str, int]) -> int:
    rows = []
    seen = set()
    for a in aliases:
        eid = key_to_id.get(a.entity_key)
        if eid is None:
            continue
        norm = compact_key(a.alias)
        dedupe = (eid, norm, a.alias)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        rows.append({"entity_id": eid, "alias": a.alias, "normalized_alias": norm, "alias_type": a.alias_type,
                     "language": a.language, "source_id": source.id, "confidence": a.confidence})
    for chunk in iter_chunks(rows, 5000):
        session.execute(insert(GeoAlias), chunk)
    return len(rows)


def entity_key_map(session: Session) -> dict[str, int]:
    return dict(session.execute(select(GeoEntity.source_record_id, GeoEntity.id)).all())  # type: ignore[arg-type]


def run_core_ingestion(session: Session, csv_path: str | None = None) -> dict:
    """Load India Post + curated data. Idempotent (replaces previous rows of these sources)."""
    t0 = time.perf_counter()
    if csv_path:
        offices, version = read_ogd_csv(csv_path), f"csv:{csv_path.rsplit('/', 1)[-1]}"
    else:
        offices, version = read_indiapins_package()
        version = f"indiapins=={version}"
    curated = load_curated()

    ip_src = ensure_source(session, "india_post", version)
    cur_src = ensure_source(session, "curated", "repo")
    run = IngestionRun(source_id=ip_src.id, records_in=len(offices))
    session.add(run)
    session.flush()

    city_suffixes: dict[tuple[str, str], set[str]] = {}
    for c in curated.cities:
        for d in c["districts"]:
            city_suffixes.setdefault((c["state"], d), set()).update([c["name"], *[a for a in c["aliases"] if a.isascii()]])
    entities, aliases, stats = build_entities(offices, curated.state_names, curated.district_names, city_suffixes)

    # Cities are curated entities; they need district centres from the India Post data.
    centers: dict[tuple[str, str], tuple[float, float]] = {}
    dataset_state = {v: k for k, v in curated.state_names.items()}
    for e in entities:
        if e.entity_type == "district" and e.latitude is not None and e.state:
            centers[(dataset_state.get(e.state, e.state.upper()), e.attributes["dataset_name"])] = (e.latitude, e.longitude)  # type: ignore[assignment]
    city_ents, city_aliases = city_entities(curated, centers)

    n_ent, n_alias = write_records(session, ip_src, entities, aliases)
    # Curated names for India Post states/districts (provenance: curated source)
    ip_keys = dict(session.execute(  # type: ignore[arg-type]
        select(GeoEntity.source_record_id, GeoEntity.id).where(GeoEntity.source_id == ip_src.id)).all())
    n_alias += _write_aliases(session, cur_src, curated_aliases(curated) + locality_alias_records(curated), ip_keys)
    # City entities belong to the curated source.
    n_city, n_city_alias = write_records(session, cur_src, city_ents, city_aliases)

    ip_src.record_count = n_ent
    cur_src.record_count = n_city
    run.records_written = n_ent + n_city
    run.status = "succeeded"
    run.finished_at = dt.datetime.now(dt.UTC)
    summary = {
        "source_version": version, "post_offices_read": len(offices), "entities": n_ent + n_city,
        "aliases": n_alias + n_city_alias, "cities": n_city, "seconds": round(time.perf_counter() - t0, 1), **stats,
    }
    log_event(log, "ingestion_complete", **summary)
    return summary


def database_is_empty(session: Session) -> bool:
    return (session.scalar(select(func.count()).select_from(GeoEntity)) or 0) == 0
