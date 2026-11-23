"""Delete resolution logs and scrub feedback text older than DATA_RETENTION_DAYS.

The API also runs this hourly in the background; use this script for cron jobs
or one-off purges:  python -m scripts.purge_retention [--days 30]
"""

from __future__ import annotations

import argparse
import json

from app.core.config import get_settings
from app.database.repositories.records import purge_expired
from app.database.session import session_scope


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    args = ap.parse_args()
    days = args.days if args.days is not None else get_settings().data_retention_days
    with session_scope() as s:
        print(json.dumps({"retention_days": days, **purge_expired(s, days)}))


if __name__ == "__main__":
    main()
