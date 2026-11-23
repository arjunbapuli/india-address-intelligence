"""Human review of customer feedback before it is used for anything.

    python -m scripts.review_feedback list                      # pending items with quality flags
    python -m scripts.review_feedback approve <id> [--alias "Hebbal" --locality-pin 560024 --locality "Hebbal Kempapura"]
    python -m scripts.review_feedback reject <id>
    python -m scripts.review_feedback export --out reviewed.jsonl   # approved items -> eval/fine-tune data

Approving can promote a reviewed alias (a name customers use for a known post-office
locality) into the gazetteer with alias_type='feedback'. Nothing is promoted automatically.
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from app.database.models import Feedback, GeoAlias, GeoEntity
from app.database.session import session_scope
from app.intelligence.text import compact_key


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("approve")
    a.add_argument("id")
    a.add_argument("--alias")
    a.add_argument("--locality")
    a.add_argument("--locality-pin")
    r = sub.add_parser("reject")
    r.add_argument("id")
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    args = ap.parse_args()

    with session_scope() as s:
        if args.cmd == "list":
            for fb in s.scalars(select(Feedback).where(Feedback.review_status == "pending_review")
                                .order_by(Feedback.created_at)).all():
                print(json.dumps({"id": fb.id, "accepted": fb.accepted, "correction_was_wrong": fb.correction_was_wrong,
                                  "corrected_components": fb.corrected_components, "flags": fb.quality_flags},
                                 ensure_ascii=False))
        elif args.cmd in ("approve", "reject"):
            fb = s.get(Feedback, args.id)
            if fb is None:
                raise SystemExit("feedback not found")
            fb.review_status = "approved" if args.cmd == "approve" else "rejected"
            if args.cmd == "approve" and args.alias and args.locality and args.locality_pin:
                ent = s.scalar(select(GeoEntity).where(GeoEntity.entity_type == "locality",
                                                       GeoEntity.pincode == args.locality_pin,
                                                       GeoEntity.name == args.locality))
                if ent is None:
                    raise SystemExit("target locality not found (name + PIN must match India Post data)")
                s.add(GeoAlias(entity_id=ent.id, alias=args.alias, normalized_alias=compact_key(args.alias),
                               alias_type="feedback", language="en", confidence=0.85))
                print(f"alias '{args.alias}' -> {ent.name} ({ent.pincode}) added; restart the API to load it")
            print(f"{args.id}: {fb.review_status}")
        elif args.cmd == "export":
            rows = s.scalars(select(Feedback).where(Feedback.review_status == "approved")).all()
            with open(args.out, "w", encoding="utf-8") as fh:
                for fb in rows:
                    fh.write(json.dumps({"input": fb.original_transcript, "resolved": fb.resolved_address,
                                         "corrected": fb.corrected_address,
                                         "corrected_components": fb.corrected_components}, ensure_ascii=False) + "\n")
            print(f"exported {len(rows)} approved items to {args.out}")


if __name__ == "__main__":
    main()
