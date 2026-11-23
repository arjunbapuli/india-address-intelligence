"""Shared fixtures.

Integration tests use a temporary SQLite database seeded once per test session
with the full India Post directory (~20 s), so they exercise real data rather
than a handful of hand-picked rows.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core import config
from app.core.config import Settings
from app.core.metrics import metrics
from app.core.ratelimit import reset_limiter
from app.database.session import reset_engine
from app.geography.gazetteer import set_gazetteer

TEST_API_KEY = "test-key-123"


def make_settings(db_url: str, **overrides) -> Settings:
    base = dict(database_url=db_url, environment="test", api_keys=TEST_API_KEY, rate_limit_per_minute=100000,
                rate_limit_burst=100000, auto_seed=True, log_level="WARNING")
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory) -> str:
    """Path-based SQLite URL with the full India Post + curated data loaded."""
    path: Path = tmp_path_factory.mktemp("db") / "test.db"
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{path}"
    config.settings_override = make_settings(url)
    reset_engine()
    from app.database.session import create_all, session_scope
    from app.geography.ingestion.pipeline import database_is_empty, run_core_ingestion

    create_all()
    with session_scope() as s:
        if database_is_empty(s):
            run_core_ingestion(s)
    return url


@pytest.fixture(scope="session")
def gazetteer(seeded_db):
    from app.geography.gazetteer import Gazetteer

    config.settings_override = make_settings(seeded_db)
    reset_engine()
    return Gazetteer.load_from_db()


@pytest.fixture()
def resolver(gazetteer):
    from app.intelligence.resolution.resolver import AddressResolver

    return AddressResolver(gazetteer, make_settings("sqlite://"))


@pytest.fixture()
def client(seeded_db, gazetteer):
    from fastapi.testclient import TestClient

    config.settings_override = make_settings(seeded_db)
    reset_engine()
    reset_limiter()
    metrics.reset()
    set_gazetteer(gazetteer)
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        c.headers.update({"X-API-Key": TEST_API_KEY})
        yield c
    set_gazetteer(None)
