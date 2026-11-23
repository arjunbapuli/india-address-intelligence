"""Evaluate the resolver on a JSONL dataset and write a JSON + Markdown report.

    python -m evaluation.run_eval --dataset evaluation/datasets/test.jsonl
    python -m evaluation.run_eval --all          # dev, test and synthetic sets

For each case the report keeps: original transcript, normalised transcript,
resolved address and ground truth, so every number can be traced to examples.

Metric definitions (see docs/evaluation.md):
  locality_accuracy      predicted locality matches one of the accepted names
  no_invented_locality   when the truth has no locality, we also returned none
  state/city/district/pin accuracy  on cases that specify that field
  component_accuracy     house/unit/floor/street/cross/sector... exact match
  exact_match_accuracy   every specified field correct
  auto_resolution_rate   status exact_match/resolved (safe to use without asking)
  locality_level_rate    a locality was returned (any status)
  incorrect_correction_rate  approximate place-name corrections that were wrong
  false_confidence_rate  resolved/exact_match while the locality or state is wrong
  expected_status_accuracy   ambiguous/unresolved cases flagged as such
  calibration / ECE      accuracy per confidence bin
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path

from app.intelligence.text import compact_key

ROOT = Path(__file__).resolve().parent
BINS = [(0.0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.9), (0.9, 1.01)]
COMPONENT_KEYS = ("house_number", "unit", "floor", "plot", "street", "cross_road", "lane", "block", "sector", "phase",
                  "stage")


def _same(a: str | None, b: str | None) -> bool:
    return compact_key(a or "") == compact_key(b or "")


def locality_ok(pred: str | None, accepted: list[str] | None) -> bool | None:
    if not accepted:
        return None
    if not pred:
        return False
    keys = {compact_key(x) for x in accepted}
    p = compact_key(pred)
    if p in keys:
        return True
    # "Andheri West" (locality + spoken direction) counts if the base locality is accepted
    base = compact_key(" ".join(pred.split()[:-1])) if len(pred.split()) > 1 else p
    return base in keys


def evaluate_case(resolver, case: dict) -> dict:
    from app.intelligence.resolution.resolver import Hints

    exp = case["expected"]
    h = case.get("hints") or {}
    t0 = time.perf_counter()
    res = resolver.resolve(case["input"], Hints(city=h.get("city"), state=h.get("state"), pincode=h.get("pincode")))
    ms = (time.perf_counter() - t0) * 1000
    c = res.components
    checks: dict[str, bool] = {}
    lo = locality_ok(c.locality, exp.get("locality"))
    if lo is not None:
        checks["locality"] = lo
    elif case.get("expect_status") or not exp.get("locality"):
        checks["no_invented_locality"] = c.locality is None or res.resolution_status in ("ambiguous",)
    if exp.get("state"):
        checks["state"] = _same(c.state, exp["state"])
    if exp.get("city"):
        checks["city"] = _same(c.city, exp["city"])
    if exp.get("district"):
        checks["district"] = _same(c.district, exp["district"])
    if exp.get("postal_code"):
        checks["postal_code"] = c.postal_code == exp["postal_code"]
    comp_checks = {}
    for k, v in (exp.get("components") or {}).items():
        comp_checks[k] = _same(getattr(c, k, None), v)
    if case.get("expect_status"):
        checks["expected_status"] = res.resolution_status == case["expect_status"]

    geo_correct = checks.get("locality", checks.get("no_invented_locality", True)) and checks.get("state", True)
    corrected_place = res.component_verification.get("locality") == "corrected"
    return {
        "id": case["id"], "categories": case.get("categories", []), "coverage_gap": case.get("coverage_gap", False),
        "original": case["input"], "normalized_transcript": res.normalized_transcript,
        "resolved_address": res.normalized_address, "ground_truth": exp,
        "predicted": {k: v for k, v in c.model_dump().items() if v and k != "country"},
        "status": res.resolution_status, "confidence": res.confidence, "needs_confirmation": res.needs_confirmation,
        "checks": checks, "component_checks": comp_checks, "geo_correct": bool(geo_correct),
        "corrected_place": corrected_place, "latency_ms": round(ms, 2),
    }


def _rate(xs: list[bool]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def summarize(rows: list[dict]) -> dict:
    def collect(key: str) -> list[bool]:
        return [r["checks"][key] for r in rows if key in r["checks"]]

    comp = [v for r in rows for v in r["component_checks"].values()]
    exact = [all(r["checks"].values()) and all(r["component_checks"].values()) for r in rows]
    auto = [r["status"] in ("exact_match", "resolved") for r in rows]
    loc_level = [bool(r["predicted"].get("locality")) for r in rows]
    corrected = [r for r in rows if r["corrected_place"]]
    false_conf = [r for r in rows if r["status"] in ("exact_match", "resolved") and not r["geo_correct"]]
    confident = [r for r in rows if r["status"] in ("exact_match", "resolved")]
    calib = []
    ece = 0.0
    for lo, hi in BINS:
        b = [r for r in rows if lo <= r["confidence"] < hi]
        if not b:
            continue
        acc = sum(r["geo_correct"] for r in b) / len(b)
        avg_c = statistics.mean(r["confidence"] for r in b)
        ece += len(b) / len(rows) * abs(acc - avg_c)
        calib.append({"bin": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(b), "avg_confidence": round(avg_c, 3),
                      "accuracy": round(acc, 3)})
    return {
        "n": len(rows),
        "locality_accuracy": _rate(collect("locality")),
        "no_invented_locality": _rate(collect("no_invented_locality")),
        "state_accuracy": _rate(collect("state")),
        "city_accuracy": _rate(collect("city")),
        "district_accuracy": _rate(collect("district")),
        "pincode_accuracy": _rate(collect("postal_code")),
        "component_accuracy": _rate(comp),
        "exact_match_accuracy": _rate(exact),
        "auto_resolution_rate": _rate(auto),
        "locality_level_rate": _rate(loc_level),
        "incorrect_correction_rate": _rate([not r["geo_correct"] for r in corrected]),
        "corrections_evaluated": len(corrected),
        "false_confidence_rate": round(len(false_conf) / len(confident), 4) if confident else None,
        "precision_when_auto_resolved": round(1 - len(false_conf) / len(confident), 4) if confident else None,
        "expected_status_accuracy": _rate(collect("expected_status")),
        "calibration": calib,
        "expected_calibration_error": round(ece, 4),
        "median_latency_ms": round(statistics.median(r["latency_ms"] for r in rows), 2) if rows else None,
    }


def run(dataset: Path, resolver) -> dict:
    cases = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [evaluate_case(resolver, c) for c in cases]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        for cat in r["categories"]:
            by_cat[cat].append(r)
    return {
        "dataset": dataset.name,
        "overall": summarize(rows),
        "excluding_coverage_gaps": summarize([r for r in rows if not r["coverage_gap"]]),
        "coverage_gaps_only": summarize([r for r in rows if r["coverage_gap"]]),
        "by_category": {k: summarize(v) for k, v in sorted(by_cat.items())},
        "failures": [r for r in rows if not (all(r["checks"].values()) and all(r["component_checks"].values()))],
        "cases": rows,
    }


def markdown(report: dict) -> str:
    def row(name: str, s: dict) -> str:
        f = lambda v: "-" if v is None else (f"{v:.1%}" if isinstance(v, float) else str(v))  # noqa: E731
        return (f"| {name} | {s['n']} | {f(s['locality_accuracy'])} | {f(s['state_accuracy'])} | "
                f"{f(s['city_accuracy'])} | {f(s['pincode_accuracy'])} | {f(s['component_accuracy'])} | "
                f"{f(s['exact_match_accuracy'])} | {f(s['auto_resolution_rate'])} | "
                f"{f(s['precision_when_auto_resolved'])} | {f(s['incorrect_correction_rate'])} | "
                f"{f(s['expected_status_accuracy'])} | {s['expected_calibration_error']:.3f} |")

    head = ("| Slice | N | Locality | State | City | PIN | Components | Exact match | Auto-resolved | "
            "Precision when auto-resolved | Incorrect corrections | Ambiguous/unresolved flagged | ECE |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    lines = [f"### {report['dataset']}", "", head, row("overall", report["overall"]),
             row("excluding coverage gaps", report["excluding_coverage_gaps"])]
    if report["coverage_gaps_only"]["n"]:
        lines.append(row("coverage gaps only", report["coverage_gaps_only"]))
    lines += ["", "Calibration (overall):", "", "| Confidence | N | Avg confidence | Accuracy |", "|---|---|---|---|"]
    for b in report["overall"]["calibration"]:
        lines.append(f"| {b['bin']} | {b['n']} | {b['avg_confidence']:.2f} | {b['accuracy']:.0%} |")
    lines += ["", f"Failures ({len(report['failures'])}):", ""]
    for r in report["failures"][:40]:
        bad = [k for k, v in {**r['checks'], **r['component_checks']}.items() if not v]
        lines.append(f"- `{r['id']}` {r['original']!r} -> {r['resolved_address']!r} "
                     f"[{r['status']} {r['confidence']}] failed: {', '.join(bad)}"
                     + (" (coverage gap)" if r["coverage_gap"] else ""))
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "results"))
    args = ap.parse_args()
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    from app.core.config import get_settings
    from app.geography.gazetteer import Gazetteer
    from app.intelligence.resolution.resolver import AddressResolver

    datasets = [Path(d) for d in args.dataset]
    if args.all or not datasets:
        datasets = [ROOT / "datasets" / n for n in ("dev.jsonl", "test.jsonl", "synthetic_stt.jsonl")
                    if (ROOT / "datasets" / n).exists()]
    g = Gazetteer.load_from_db()
    resolver = AddressResolver(g, get_settings())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    md = ["# Evaluation results", "", f"Resolver run on {time.strftime('%Y-%m-%d')}. LLM disabled (deterministic "
          "pipeline only). See docs/evaluation.md for definitions and caveats.", ""]
    for d in datasets:
        rep = run(d, resolver)
        (out / f"{d.stem}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        md.append(markdown(rep))
        o = rep["overall"]
        print(f"{d.name}: n={o['n']} locality={o['locality_accuracy']} state={o['state_accuracy']} "
              f"exact={o['exact_match_accuracy']} auto={o['auto_resolution_rate']} "
              f"precision_auto={o['precision_when_auto_resolved']} ECE={o['expected_calibration_error']}")
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")


if __name__ == "__main__":
    main()
