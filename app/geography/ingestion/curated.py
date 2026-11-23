"""Curated reference data maintained in this repository (datasets/curated/*.json).

These files hold *naming knowledge* that no open dataset provides cleanly:
official vs. colloquial state names, renamed districts (Gurgaon -> Gurugram),
and which districts make up a city. They are small, reviewed by hand, and
validated against the India Post data by tests/unit/test_curated_data.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.geography.ingestion.india_post import AliasRecord, EntityRecord
from app.intelligence.text import smart_title

CURATED_DIR = Path(__file__).resolve().parents[3] / "datasets" / "curated"

# Two-letter state codes that are also common English words / address tokens.
_UNSAFE_CODES = {"KA", "OR", "GA", "AS", "PY", "CH", "LA", "DL", "AN", "ML", "MN", "TR", "UK", "DH", "BR", "AR", "LD",
                 "HR", "PB", "SK", "NL", "MZ", "JH", "CT", "GJ", "RJ", "KL", "JK", "MH", "TG", "TS"}


def _script_lang(text: str) -> str | None:
    for ch in text:
        cp = ord(ch)
        if 0x0900 <= cp <= 0x097F:
            return "hi"
        if 0x0980 <= cp <= 0x09FF:
            return "bn"
        if 0x0A00 <= cp <= 0x0A7F:
            return "pa"
        if 0x0A80 <= cp <= 0x0AFF:
            return "gu"
        if 0x0B00 <= cp <= 0x0B7F:
            return "or"
        if 0x0B80 <= cp <= 0x0BFF:
            return "ta"
        if 0x0C00 <= cp <= 0x0C7F:
            return "te"
        if 0x0C80 <= cp <= 0x0CFF:
            return "kn"
        if 0x0D00 <= cp <= 0x0D7F:
            return "ml"
    return None


@dataclass
class CuratedData:
    states: list[dict]
    districts: dict[str, dict]
    cities: list[dict]
    locality_aliases: list[dict] = field(default_factory=list)
    state_names: dict[str, str] = field(default_factory=dict)  # dataset -> canonical
    district_names: dict[tuple[str, str], str] = field(default_factory=dict)


def load_curated(directory: Path = CURATED_DIR) -> CuratedData:
    states = json.loads((directory / "states.json").read_text(encoding="utf-8"))["states"]
    districts = json.loads((directory / "districts.json").read_text(encoding="utf-8"))["districts"]
    cities = json.loads((directory / "cities.json").read_text(encoding="utf-8"))["cities"]
    loc_path = directory / "locality_aliases.json"
    loc = json.loads(loc_path.read_text(encoding="utf-8"))["aliases"] if loc_path.exists() else []
    data = CuratedData(states=states, districts=districts, cities=cities, locality_aliases=loc)
    data.state_names = {s["dataset_name"]: s["name"] for s in states}
    for key, v in districts.items():
        st, di = key.split("|", 1)
        data.district_names[(st, di)] = v["name"]
    return data


def curated_aliases(data: CuratedData) -> list[AliasRecord]:
    """Aliases for state and district entities created by the India Post builder."""
    out: list[AliasRecord] = []
    for s in data.states:
        key = f"state:{s['dataset_name']}"
        for a in s.get("aliases", []):
            if a.upper() in _UNSAFE_CODES:
                continue
            lang = _script_lang(a)
            out.append(AliasRecord(key, a, "transliteration" if lang else "alternate_name", lang or "en", 0.95))
        code = s.get("code")
        if code and code.upper() not in _UNSAFE_CODES:
            out.append(AliasRecord(key, code, "abbreviation", "en", 0.8))
    for k, v in data.districts.items():
        st, di = k.split("|", 1)
        for a in v.get("aliases", []):
            out.append(AliasRecord(f"district:{st}:{di}", a, "old_name", _script_lang(a) or "en", 0.9))
    return out


def locality_alias_records(data: CuratedData) -> list[AliasRecord]:
    """Aliases pointing at India Post locality entities (keys 'po:<pin>:<lower name>')."""
    return [
        AliasRecord(f"po:{a['pincode']}:{a['locality'].lower()}", a["alias"], a.get("type", "alternate_name"), "en", 0.9)
        for a in data.locality_aliases
    ]


def city_entities(data: CuratedData, district_centers: dict[tuple[str, str], tuple[float, float]]) -> tuple[
    list[EntityRecord], list[AliasRecord]
]:
    ents: list[EntityRecord] = []
    aliases: list[AliasRecord] = []
    for c in data.cities:
        st = c["state"]
        canonical_districts = [data.district_names.get((st, d), smart_title(d)) for d in c["districts"]]
        infer = [data.district_names.get((st, d), smart_title(d)) for d in c.get("infer_districts", [])]
        center = district_centers.get((st, c["districts"][0])) if c["districts"] else None
        key = f"city:{st}:{c['name']}"
        ents.append(
            EntityRecord(
                key=key, entity_type="city", name=c["name"], state=data.state_names.get(st, smart_title(st)),
                district=canonical_districts[0] if canonical_districts else None, city=c["name"], pincode=None,
                latitude=center[0] if center else None, longitude=center[1] if center else None,
                source_confidence=0.95,
                attributes={"districts": canonical_districts, "infer_districts": infer, "tier": c.get("tier", 2)},
            )
        )
        for a in c.get("aliases", []):
            lang = _script_lang(a)
            aliases.append(AliasRecord(key, a, "transliteration" if lang else "alternate_name", lang or "en", 0.95))
    return ents, aliases
