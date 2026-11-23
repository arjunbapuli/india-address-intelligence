"""Load geographic data into the database.

Usage:
    python -m scripts.ingest                      # India Post (bundled) + curated data
    python -m scripts.ingest --if-empty           # only if the database has no entities yet
    python -m scripts.ingest --csv pincodes.csv   # use a data.gov.in CSV instead of the bundled copy
    python -m scripts.ingest --osm india.osm.pbf  # add OpenStreetMap places/landmarks/streets
"""

from __future__ import annotations

import argparse
import json
import sys

from app.core.logging import configure_logging
from app.database.session import create_all, session_scope
from app.geography.ingestion.pipeline import database_is_empty, run_core_ingestion


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="data.gov.in All India Pincode Directory CSV")
    ap.add_argument("--osm", help="OpenStreetMap extract (.osm.pbf or .osm) to import")
    ap.add_argument("--if-empty", action="store_true", help="skip core ingestion if data already present")
    ap.add_argument("--create-tables", action="store_true", help="create tables without Alembic (SQLite quick start)")
    args = ap.parse_args(argv)
    configure_logging("INFO")
    if args.create_tables:
        create_all()
    with session_scope() as s:
        if args.if_empty and not database_is_empty(s):
            print(json.dumps({"skipped": "database already populated"}))
        else:
            print(json.dumps(run_core_ingestion(s, csv_path=args.csv), indent=2))
    if args.osm:
        from app.geography.ingestion.osm import run_osm_ingestion

        with session_scope() as s:
            print(json.dumps(run_osm_ingestion(s, args.osm), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
