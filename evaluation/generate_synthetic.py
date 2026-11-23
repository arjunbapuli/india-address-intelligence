"""Generate a SYNTHETIC speech-to-text noise set from the India Post directory.

    python -m evaluation.generate_synthetic --n 500 --seed 7

This is a robustness test, not an accuracy estimate: the place names come from
the same data the resolver searches, so it measures how well matching survives
STT-like corruption (dropped vowels, aspirate confusion, doubled consonants,
split/merged words, spoken numbers) across all states. It says nothing about
coverage of places missing from the data. Results are reported separately and
labelled synthetic.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

from app.intelligence.normalization.numbers import EN_UNITS
from app.intelligence.text import compact_key

NUM_WORDS = {v: k for k, v in EN_UNITS.items() if v < 20}


def corrupt(name: str, rng: random.Random) -> tuple[str, str]:
    ops = ["none", "drop_vowel", "aspirate", "double", "split_merge", "v_w", "vowel_len"]
    op = rng.choice(ops)
    s = name.lower()
    if op == "drop_vowel":
        idx = [i for i, ch in enumerate(s) if ch in "aeiou" and 0 < i < len(s) - 1]
        if idx:
            i = rng.choice(idx)
            s = s[:i] + s[i + 1:]
    elif op == "aspirate":
        if re.search(r"([kgcjtdpb])h", s):
            s = re.sub(r"([kgcjtdpb])h", r"\1", s, count=1)
        else:
            s = re.sub(r"([kgtdb])(?=[aeiou])", r"\1h", s, count=1)
    elif op == "double":
        m = re.search(r"([bcdfgklmnprstv])\1", s)
        s = s.replace(m.group(0), m.group(1), 1) if m else re.sub(r"([lnrt])(?=[aeiou])", r"\1\1", s, count=1)
    elif op == "split_merge":
        s = s.replace(" ", "") if " " in s else re.sub(r"(nagar|pur|halli|palli|gaon|abad|ganj)$", r" \1", s)
    elif op == "v_w":
        s = s.replace("v", "w", 1) if "v" in s else s.replace("w", "v", 1)
    elif op == "vowel_len":
        s = s.replace("ee", "i", 1) if "ee" in s else s.replace("i", "ee", 1)
    return s, op


def spoken_pin(pin: str) -> str:
    return " ".join(NUM_WORDS[int(d)] for d in pin)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "datasets" / "synthetic_stt.jsonl"))
    args = ap.parse_args()
    from app.geography.gazetteer import Gazetteer

    g = Gazetteer.load_from_db()
    rng = random.Random(args.seed)
    # Delivery post offices with a unique name inside their district, stratified by state
    by_state: dict[str, list] = {}
    name_count: dict[tuple, int] = {}
    for p in g.places.values():
        if p.type == "locality" and p.district and p.state:
            name_count[(compact_key(p.name), p.state, p.district)] = name_count.get(
                (compact_key(p.name), p.state, p.district), 0) + 1
    for p in g.places.values():
        if (p.type == "locality" and p.delivery and p.state and p.district and len(compact_key(p.name)) >= 5
                and name_count[(compact_key(p.name), p.state, p.district)] == 1):
            by_state.setdefault(p.state, []).append(p)
    states = sorted(by_state)
    rows = []
    for i in range(args.n):
        st = states[i % len(states)]
        p = rng.choice(by_state[st])
        noisy, op = corrupt(p.name, rng)
        house = rng.choice(["", f"house no {rng.randint(1, 300)}, ", f"{rng.randint(1, 99)}, ", "flat 2b, "])
        ctx = rng.random()
        if ctx < 0.45:
            tail, ctx_kind = f", {p.district.lower()}", "district"
        elif ctx < 0.65:
            tail, ctx_kind = f", {p.district.lower()} {p.state.lower()}", "district+state"
        elif ctx < 0.85:
            tail, ctx_kind = f", {spoken_pin(p.pincode) if rng.random() < 0.5 else p.pincode}", "pincode"
        else:
            tail, ctx_kind = "", "none"
        rows.append({
            "id": f"s{i:04d}", "input": f"{house}{noisy}{tail}", "hints": {},
            "categories": ["synthetic", f"noise:{op}", f"context:{ctx_kind}"], "coverage_gap": False,
            "expected": {"locality": [p.name], "city": None, "district": p.district, "state": p.state,
                         "postal_code": p.pincode if ctx_kind == "pincode" else None, "components": {}},
            "expect_status": None,
        })
    Path(args.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"wrote {len(rows)} synthetic cases to {args.out}")


if __name__ == "__main__":
    main()
