"""Database, ingestion, curated-data integrity, OSM import, Alembic migration and retention."""

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import create_engine, func, inspect, select

from app.core import config
from app.database.models import DataSource, Feedback, GeoAlias, GeoEntity, ResolutionLog
from app.database.repositories.records import purge_expired
from app.database.session import reset_engine, session_scope
from tests.conftest import make_settings

CURATED = Path(__file__).resolve().parents[2] / "datasets" / "curated"


def test_ingestion_counts_and_provenance(seeded_db):
    with session_scope() as s:
        counts = dict(s.execute(select(GeoEntity.entity_type, func.count()).group_by(GeoEntity.entity_type)).all())
        assert counts["state"] == 36
        assert counts["district"] >= 700
        assert counts["pincode"] > 19000
        assert counts["locality"] > 160000
        assert counts["city"] >= 140
        src = {d.key: d for d in s.scalars(select(DataSource)).all()}
        assert "GODL" in src["india_post"].license and src["india_post"].version.startswith("indiapins==")
        assert s.scalar(select(func.count()).select_from(GeoAlias)) > 1000


def test_curated_cities_reference_real_districts(gazetteer):
    data = json.loads((CURATED / "cities.json").read_text())["cities"]
    for c in data:
        for d in c["districts"]:
            # every district a city points to must exist in the India Post data (after display-name mapping)
            matches = [p for p in gazetteer.places.values() if p.type == "district"
                       and (p.state or "").upper().replace("THE ", "") in c["state"].replace("THE ", "")]
            assert matches, c["name"]
    assert all(info.districts for info in gazetteer.cities.values())


def test_curated_locality_aliases_point_to_existing_post_offices(gazetteer):
    data = json.loads((CURATED / "locality_aliases.json").read_text())["aliases"]
    for a in data:
        hits = [gazetteer.places[h.place_id] for h in gazetteer.lookup_exact(a["alias"])]
        assert any(p.name == a["locality"] and p.pincode == a["pincode"] for p in hits), a


def test_every_state_has_localities_and_pincodes(gazetteer):
    states = [p.state for p in gazetteer.places.values() if p.type == "locality"]
    assert len(set(states) - {None}) == 36
    assert states.count(None) < 10  # a handful of source rows have no state and no PIN siblings to infer from


def test_alembic_migration_runs_on_sqlite(tmp_path):
    from alembic import command
    from alembic.config import Config

    url = f"sqlite:///{tmp_path / 'mig.db'}"
    prev = config.settings_override
    config.settings_override = make_settings(url)
    try:
        cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        command.upgrade(cfg, "head")
        tables = set(inspect(create_engine(url)).get_table_names())
        assert {"geo_entities", "geo_aliases", "data_sources", "resolution_logs", "feedback"} <= tables
        command.downgrade(cfg, "base")
    finally:
        config.settings_override = prev


def test_retention_purge(seeded_db):
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=40)
    with session_scope() as s:
        s.add(ResolutionLog(id="old-log", endpoint="resolve", input_fingerprint="x", input_length=3, status="resolved",
                            confidence=0.9, needs_confirmation=False, latency_ms=1.0, resolver_version="0.1.0",
                            input_text="12 secret road", created_at=old))
        s.add(Feedback(id="old-fb", original_transcript="secret", corrected_address="secret addr", created_at=old))
    with session_scope() as s:
        res = purge_expired(s, 30)
    assert res["resolution_logs_deleted"] >= 1 and res["feedback_scrubbed"] >= 1
    with session_scope() as s:
        assert s.get(ResolutionLog, "old-log") is None
        fb = s.get(Feedback, "old-fb")
        assert fb.original_transcript is None and fb.corrected_address is None


def test_osm_import_and_landmark_correction(tmp_path, seeded_db):
    """Import the synthetic OSM fixture into a copy of the DB and check landmark/neighbourhood use."""
    import shutil

    from app.geography.gazetteer import Gazetteer
    from app.geography.ingestion.osm import acronym, run_osm_ingestion
    from app.intelligence.resolution.resolver import AddressResolver

    assert acronym("National Institute of Fashion Technology") == "NIFT"
    src_path = seeded_db.replace("sqlite:///", "")
    if not Path(src_path).exists():  # TEST_DATABASE_URL pointing at Postgres: skip the copy-based test
        return
    copy = tmp_path / "osm.db"
    shutil.copy(src_path, copy)
    prev = config.settings_override
    config.settings_override = make_settings(f"sqlite:///{copy}")
    reset_engine()
    try:
        with session_scope() as s:
            summary = run_osm_ingestion(s, str(Path(__file__).resolve().parents[1] / "fixtures" / "mini.osm"))
        assert summary["landmark"] == 2 and summary["osm_place"] == 1 and summary["street"] == 1
        g = Gazetteer.load_from_db()
        r = AddressResolver(g, make_settings("sqlite://"))
        res = r.resolve("14 main hsr sector to near nifty bangalore")
        assert res.components.landmark == "National Institute of Fashion Technology"
        assert res.component_verification["landmark"] == "corrected"
        assert res.component_verification["street"] == "verified"
        res = r.resolve("btm layout 2nd stage bangalore")
        assert res.components.locality == "BTM Layout"
        assert res.components.postal_code is None  # no PIN inferred from an OSM-only place
    finally:
        config.settings_override = prev
        reset_engine()
