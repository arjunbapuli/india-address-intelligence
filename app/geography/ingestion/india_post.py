"""India Post 'All India Pincode Directory' ingestion.

Sources supported (same underlying dataset, published by India Post on the
Open Government Data Platform, https://data.gov.in, under the Government Open
Data License - India):

1. `indiapins` PyPI package (default). It ships the directory as a compressed
   JSON file (165k post offices, updated 2026-02-21) and is pinned in
   pyproject.toml so ingestion is reproducible offline.
2. A CSV downloaded from data.gov.in ("All India Pincode Directory till last
   month" / "with lat long"), passed with `--csv path/to/file.csv`.

Each post office becomes a `locality` entity (post-office area, town or
village). We also derive `pincode`, `district` and `state` entities.
"""

from __future__ import annotations

import bz2
import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from app.intelligence.normalization.numbers import roman_to_int
from app.intelligence.text import ordinal, smart_title

# India bounding box (generous) for sanity-checking coordinates
LAT_RANGE = (6.0, 37.6)
LON_RANGE = (68.0, 97.6)

CIRCLE_TO_STATE = {
    "andhra pradesh circle": "ANDHRA PRADESH", "assam circle": "ASSAM", "bihar circle": "BIHAR",
    "chattisgarh circle": "CHHATTISGARH", "chhattisgarh circle": "CHHATTISGARH", "delhi circle": "DELHI",
    "gujarat circle": "GUJARAT", "haryana circle": "HARYANA", "himachal pradesh circle": "HIMACHAL PRADESH",
    "jammu & kashmir circle": "JAMMU AND KASHMIR", "jharkhand circle": "JHARKHAND",
    "karnataka circle": "KARNATAKA", "kerala circle": "KERALA", "madhya pradesh circle": "MADHYA PRADESH",
    "maharashtra circle": "MAHARASHTRA", "odisha circle": "ODISHA", "punjab circle": "PUNJAB",
    "rajasthan circle": "RAJASTHAN", "tamilnadu circle": "TAMIL NADU", "tamil nadu circle": "TAMIL NADU",
    "telangana circle": "TELANGANA", "uttar pradesh circle": "UTTAR PRADESH",
    "uttarakhand circle": "UTTARAKHAND", "west bengal circle": "WEST BENGAL",
}


@dataclass(slots=True)
class PostOffice:
    name: str
    pincode: str
    office_type: str  # BO / PO (sub office) / HO
    delivery: bool
    district: str | None
    state: str | None
    circle: str | None
    division: str | None
    latitude: float | None
    longitude: float | None


def _f(v: object) -> float | None:
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return x


def _valid_coords(lat: float | None, lon: float | None) -> bool:
    return (
        lat is not None and lon is not None
        and LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]
    )


def read_indiapins_package() -> tuple[list[PostOffice], str]:
    """Read the directory bundled with the pinned `indiapins` package."""
    import indiapins  # noqa: F401  (ensures the package is installed)

    data = resources.files("indiapins").joinpath("pins.json.bz2").read_bytes()
    rows = []
    for line in bz2.decompress(data).decode("utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    try:
        from importlib.metadata import version

        ver = version("indiapins")
    except Exception:  # pragma: no cover
        ver = "unknown"
    return [_from_indiapins(r) for r in rows], ver


def _from_indiapins(r: dict) -> PostOffice:
    return PostOffice(
        name=str(r.get("Name") or "").strip(),
        pincode=str(r.get("Pincode") or "").strip(),
        office_type=str(r.get("BranchType") or "").strip().upper(),
        delivery=str(r.get("DeliveryStatus") or "").strip().lower() == "delivery",
        district=(r.get("District") or None),
        state=(r.get("State") or None),
        circle=r.get("Circle"),
        division=r.get("Division"),
        latitude=_f(r.get("Latitude")),
        longitude=_f(r.get("Longitude")),
    )


def read_ogd_csv(path: str | Path) -> list[PostOffice]:
    """Read the data.gov.in CSV (column names are matched case-insensitively)."""
    out: list[PostOffice] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            r = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
            office_type = r.get("officetype", "").upper()
            office_type = {"S.O": "PO", "SO": "PO", "B.O": "BO", "H.O": "HO"}.get(office_type, office_type)
            out.append(
                PostOffice(
                    name=r.get("officename", ""),
                    pincode=r.get("pincode", ""),
                    office_type=office_type,
                    delivery=r.get("delivery", r.get("deliverystatus", "")).lower().startswith("deliv"),
                    district=r.get("district") or r.get("districtname") or None,
                    state=r.get("statename") or None,
                    circle=r.get("circlename"),
                    division=r.get("divisionname"),
                    latitude=_f(r.get("latitude")),
                    longitude=_f(r.get("longitude")),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Name cleaning
# ---------------------------------------------------------------------------

_OFFICE_SUFFIX = re.compile(
    r"\s+(?:B\.?\s?O\.?|S\.?\s?O\.?|H\.?\s?O\.?|G\.?\s?P\.?\s?O\.?|P\.?\s?O\.?|E\.?\s?D\.?\s?B\.?\s?O\.?|MDG|NDC|"
    r"H\.Q\.?|NSH|TSO|SPO)\b.*$",
    re.IGNORECASE,
)
_PARENS = re.compile(r"\s*\(([^)]*)\)\s*")
_ROMAN_UNIT = re.compile(
    r"\b(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\s+(Block|Bk|Blk|Phase|Ph|Stage|Stg|Sector|Sec|Cross|Main)\b",
    re.IGNORECASE,
)
_UNIT_ROMAN = re.compile(r"\b(Block|Phase|Stage|Sector)\s+(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)\b",
                         re.IGNORECASE)
_NUM_UNIT = re.compile(r"\b(\d{1,2})\s+(Block|Bk|Blk|Phase|Stage|Cross|Main)\b", re.IGNORECASE)
_NAME_ABBR = {
    "blk": "Block", "ph": "Phase", "stg": "Stage", "extn": "Extension", "ext": "Extension",
    "ngr": "Nagar", "rd": "Road", "mkt": "Market", "colly": "Colony", "clny": "Colony", "indl": "Industrial",
    "estt": "Estate", "sec": "Sector",
}
_UNIT_CANON = {"bk": "Block", "blk": "Block", "ph": "Phase", "stg": "Stage", "sec": "Sector"}


def clean_post_office_name(raw: str) -> tuple[str, str | None]:
    """'Koramangala VI Bk S.O' -> ('Koramangala 6th Block', None)
    'Indiranagar S.O (Bengaluru)' -> ('Indiranagar', 'Bengaluru')
    'Malviya Nagar SO South Delhi' -> ('Malviya Nagar', None)
    """
    name = raw.strip()
    hint = None
    m = _PARENS.search(name)
    if m:
        hint = m.group(1).strip() or None
        name = _PARENS.sub(" ", name).strip()
    name = _OFFICE_SUFFIX.sub("", name).strip()
    name = re.sub(r"\s+(BO|SO|HO|B\.O|S\.O|H\.O)$", "", name, flags=re.IGNORECASE).strip()
    # Join spaced single-letter acronyms: "E P I P" -> "EPIP"
    acronyms: list[str] = []

    def _join(mm: re.Match[str]) -> str:
        acronyms.append(mm.group(1).replace(" ", ""))
        return acronyms[-1]

    name = re.sub(r"\b((?:[A-Z]\s){1,5}[A-Z])\b", _join, name)

    def _roman(mm: re.Match[str]) -> str:
        n = roman_to_int(mm.group(1))
        unit = _UNIT_CANON.get(mm.group(2).lower(), mm.group(2).title())
        if unit == "Sector":
            return f"Sector {n}"
        return f"{ordinal(n)} {unit}" if n else mm.group(0)

    name = re.sub(r"\bSec\.?\s?-?(\d{1,3})\b", r"Sector \1", name, flags=re.IGNORECASE)
    name = re.sub(r"\bPh\.?\s?(I{1,3}|IV|V|VI{0,3}|\d)\b", r"Phase \1", name)
    name = _ROMAN_UNIT.sub(_roman, name)
    name = _UNIT_ROMAN.sub(lambda mm: f"{mm.group(1).title()} {roman_to_int(mm.group(2))}", name)
    name = _NUM_UNIT.sub(lambda mm: f"{ordinal(int(mm.group(1)))} {_UNIT_CANON.get(mm.group(2).lower(), mm.group(2).title())}", name)
    words = [(_NAME_ABBR.get(w.lower().rstrip("."), w) if len(w) <= 5 else w) for w in name.split()]
    name = " ".join(words)
    name = re.sub(r"\s{2,}", " ", name).strip(" .-,")
    titled = smart_title(name)
    for a in acronyms:
        titled = re.sub(rf"\b{a}\b", a, titled, flags=re.IGNORECASE)
    return titled, hint


# ---------------------------------------------------------------------------
# Entity building
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EntityRecord:
    key: str  # stable natural key within the source, e.g. "po:560102:hsr layout"
    entity_type: str
    name: str
    state: str | None
    district: str | None
    city: str | None
    pincode: str | None
    latitude: float | None
    longitude: float | None
    source_confidence: float
    attributes: dict


@dataclass(slots=True)
class AliasRecord:
    entity_key: str
    alias: str
    alias_type: str
    language: str | None = None
    confidence: float = 0.9


def fill_missing_admin(offices: list[PostOffice]) -> None:
    """Some rows lack state/district. Infer from other offices with the same PIN, else the postal circle."""
    by_pin: dict[str, Counter] = defaultdict(Counter)
    for o in offices:
        if o.state and o.district:
            by_pin[o.pincode][(o.state, o.district)] += 1
    for o in offices:
        if o.state and o.district:
            continue
        if by_pin.get(o.pincode):
            st, di = by_pin[o.pincode].most_common(1)[0][0]
            o.state = o.state or st
            o.district = o.district or di
        elif o.circle and not o.state:
            o.state = CIRCLE_TO_STATE.get(o.circle.strip().lower())


def _median(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def strip_area_suffix(name: str, suffixes: set[str]) -> str | None:
    """'Lajpat Nagar South Delhi' -> 'Lajpat Nagar' when 'South Delhi' is the district.
    Returns None when nothing should be stripped (or the remainder would be generic)."""
    low = name.lower()
    for suf in sorted(suffixes, key=len, reverse=True):
        sl = suf.lower()
        if low.endswith(" " + sl):
            rest = name[: -len(suf)].strip(" -,")
            words = rest.lower().split()
            if len(rest) >= 3 and not all(w in ("sector", "phase", "block", "stage") or w.isdigit() for w in words):
                return rest
    return None


def build_entities(
    offices: list[PostOffice],
    state_names: dict[str, str],
    district_names: dict[tuple[str, str], str],
    city_suffixes: dict[tuple[str, str], set[str]] | None = None,
) -> tuple[list[EntityRecord], list[AliasRecord], dict]:
    """Derive state, district, pincode and locality entities from post-office rows.

    state_names: dataset state name -> canonical display name
    district_names: (dataset state, dataset district) -> canonical display name
    Returns (entities, aliases, stats).
    """
    fill_missing_admin(offices)
    stats: Counter = Counter()

    def st_name(s: str | None) -> str | None:
        return state_names.get(s, smart_title(s)) if s else None

    def di_name(s: str | None, d: str | None) -> str | None:
        if not d:
            return None
        return district_names.get((s or "", d), smart_title(d))

    # District medians for outlier coordinate detection
    dist_coords: dict[tuple, list[tuple[float, float]]] = defaultdict(list)
    for o in offices:
        if _valid_coords(o.latitude, o.longitude):
            dist_coords[(o.state, o.district)].append((o.latitude, o.longitude))  # type: ignore[arg-type]
    dist_center = {
        k: (statistics.median(a for a, _ in v), statistics.median(b for _, b in v)) for k, v in dist_coords.items()
    }

    entities: list[EntityRecord] = []
    aliases: list[AliasRecord] = []
    pin_offices: dict[str, list[PostOffice]] = defaultdict(list)
    seen_loc: set[str] = set()

    for o in offices:
        if not re.fullmatch(r"[1-9]\d{5}", o.pincode or ""):
            stats["skipped_bad_pincode"] += 1
            continue
        pin_offices[o.pincode].append(o)
        name, hint = clean_post_office_name(o.name)
        if not name:
            stats["skipped_empty_name"] += 1
            continue
        suffixes = set((city_suffixes or {}).get((o.state or "", o.district or ""), set()))
        if o.district:
            suffixes.add(di_name(o.state, o.district) or "")
            suffixes.add(o.district)
        long_name = None
        stripped_name = strip_area_suffix(name, {x for x in suffixes if x})
        if stripped_name:
            long_name, name = name, stripped_name
        lat, lon = (o.latitude, o.longitude) if _valid_coords(o.latitude, o.longitude) else (None, None)
        coord_quality = "post_office" if lat is not None else None
        if lat is not None:
            c = dist_center.get((o.state, o.district))
            if c and _haversine_km(lat, lon, c[0], c[1]) > 150:  # type: ignore[arg-type]
                lat, lon, coord_quality = None, None, None
                stats["dropped_outlier_coords"] += 1
        key = f"po:{o.pincode}:{name.lower()}"
        if key in seen_loc:
            stats["duplicate_po_names"] += 1
            continue
        seen_loc.add(key)
        entities.append(
            EntityRecord(
                key=key, entity_type="locality", name=name, state=st_name(o.state), district=di_name(o.state, o.district),
                city=None, pincode=o.pincode, latitude=lat, longitude=lon,
                source_confidence=0.9 if o.delivery else 0.8,
                attributes={
                    "office_name": o.name, "office_type": o.office_type, "delivery": o.delivery,
                    "division": o.division, "coord_quality": coord_quality, "name_hint": hint,
                },
            )
        )
        stats["localities"] += 1
        # Acronym aliases derived from data: "HSR Layout" -> "HSR", "BTM Layout" -> "BTM"
        m = re.fullmatch(r"([A-Z]{3,5})\s+(Layout|Nagar|Colony|Extension|Town|Puram|Vihar|Enclave|Complex|Estate)", name)
        if m:
            aliases.append(AliasRecord(key, m.group(1), "acronym", "en", 0.85))
        if long_name:
            aliases.append(AliasRecord(key, long_name, "alternate_name", "en", 0.95))
        # Original India Post spelling is also an alias ("Koramangala VI Bk S.O")
        stripped = _OFFICE_SUFFIX.sub("", o.name).strip()
        if stripped and stripped.lower() != name.lower():
            aliases.append(AliasRecord(key, stripped, "alternate_name", "en", 0.9))

    # Pincodes
    for pin, offs in pin_offices.items():
        sd = Counter((o.state, o.district) for o in offs if o.state).most_common(1)
        st, di = sd[0][0] if sd else (None, None)
        lats = [o.latitude for o in offs if _valid_coords(o.latitude, o.longitude)]
        lons = [o.longitude for o in offs if _valid_coords(o.latitude, o.longitude)]
        districts = sorted({di_name(o.state, o.district) for o in offs if o.district} - {None})  # type: ignore[arg-type]
        entities.append(
            EntityRecord(
                key=f"pin:{pin}", entity_type="pincode", name=pin, state=st_name(st), district=di_name(st, di), city=None,
                pincode=pin, latitude=_median(lats), longitude=_median(lons), source_confidence=0.95,
                attributes={
                    "offices": len(offs), "delivery_offices": sum(o.delivery for o in offs), "districts": districts,
                    "states": sorted({st_name(o.state) for o in offs if o.state} - {None}),  # type: ignore[arg-type]
                },
            )
        )
        stats["pincodes"] += 1

    # Districts
    dist_pins: dict[tuple, set[str]] = defaultdict(set)
    for o in offices:
        if o.state and o.district:
            dist_pins[(o.state, o.district)].add(o.pincode)
    for (s, d), pins in dist_pins.items():
        c = dist_center.get((s, d))
        entities.append(
            EntityRecord(
                key=f"district:{s}:{d}", entity_type="district", name=di_name(s, d) or d, state=st_name(s), district=di_name(s, d),
                city=None, pincode=None, latitude=c[0] if c else None, longitude=c[1] if c else None,
                source_confidence=0.95, attributes={"dataset_name": d, "pincode_count": len(pins)},
            )
        )
        if (di_name(s, d) or "").lower() != d.lower():
            aliases.append(AliasRecord(f"district:{s}:{d}", smart_title(d), "alternate_name", "en", 0.95))
        stats["districts"] += 1

    # States
    state_set = sorted({o.state for o in offices if o.state})
    for s in state_set:
        entities.append(
            EntityRecord(key=f"state:{s}", entity_type="state", name=st_name(s) or s, state=st_name(s), district=None,
                         city=None, pincode=None, latitude=None, longitude=None, source_confidence=0.99,
                         attributes={"dataset_name": s})
        )
        stats["states"] += 1
    return entities, aliases, dict(stats)


def iter_chunks(items: Iterable, size: int) -> Iterator[list]:
    buf: list = []
    for it in items:
        buf.append(it)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf
