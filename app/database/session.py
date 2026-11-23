"""Engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False}, future=True)

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, future=True)


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _make_engine(get_settings().database_url)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def db_healthy() -> tuple[bool, str | None]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # pragma: no cover - depends on infra
        return False, type(exc).__name__


def create_all() -> None:
    """Create tables directly (tests / SQLite quick-start). Production uses Alembic."""
    from app.database.models import Base

    Base.metadata.create_all(get_engine())
