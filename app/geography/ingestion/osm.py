"""OpenStreetMap importer: neighbourhoods, streets and landmarks.

Input: any OSM extract readable by pyosmium (.osm.pbf or .osm XML). For all of
India download https://download.geofabrik.de/asia/india-latest.osm.pbf
(~1.5 GB; or regional extracts such as india/southern-zone). Data is
© OpenStreetMap contributors, ODbL 1.0 — attribution is stored with every row.

What is imported:
  * place=suburb|neighbourhood|quarter|locality|village|town|city|hamlet -> `osm_place`
  * named amenities / tourism / shops / stations / parks                 -> `landmark`
  * named roads (highway=primary..residential)                           -> `street`
Each entity gets district/state/PIN from `addr:postcode` when valid, else
from the nearest India Post office (so OSM entities join the same hierarchy).
Name variants (name:en, alt_name, old_name, short_name, name:hi, name:kn ...)
become aliases; multi-word institution names also get an acronym alias
("National Institute of Fashion Technology" -> "NIFT").

Requires the optional dependency: pip install "india-address-intelligence[osm]".
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_event
from app.database.models import GeoEntity
from app.geography.gazetteer import haversine_km
from app.geography.ingestion.india_post import AliasRecord, EntityRecord
from app.geography.ingestion.pipeline import ensure_source, write_records

log = get_logger(__name__)

PLACE_VALUES = {"suburb", "neighbourhood", "quarter", "locality", "village", "town", "city", "hamlet"}
LANDMARK_TAGS = {
    "amenity": {"university", "college", "school", "hospital", "clinic", "place_of_worship", "bus_station",
                "marketplace", "cinema", "police", "post_office", "townhall", "library", "theatre", "courthouse",
                "fire_station", "community_centre", "fuel", "bank"},
    "tourism": {"attraction", "museum", "hotel", "zoo", "theme_park", "viewpoint"},
    "shop": {"mall", "department_store", "supermarket"},
    "railway": {"station", "halt"},
    "public_transport": {"station"},
    "aeroway": {"aerodrome", "terminal"},
    "leisure": {"park", "stadium", "sports_centre", "garden"},
    "historic": {"monument", "memorial", "fort", "castle"},
    "office": {"government", "it", "company"},
    "landuse": {"commercial", "industrial"},
}
STREET_VALUES = {"trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street"}
NAME_KEYS = ("name:en", "alt_name", "old_name", "short_name", "official_name", "loc_name", "name:hi", "name:kn",
             "name:ta", "name:te", "name:ml", "name:bn", "name:mr", "name:gu", "name:pa", "name:or")
_STOP = {"of", "and", "the", "for", "&", "de"}


@dataclass(slots=True)
class OsmFeature:
    osm_id: str
    kind: str  # osm_place | landmark | street
    name: str
    lat: float
    lon: float
    alt_names: list[tuple[str, str]]  # (alias, language)
    postcode: str | None
    subtype: str


def acronym(name: str) -> str | None:
    words = [w for w in re.split(r"[\s\-]+", name) if w and w.lower() not in _STOP]
    if len(words) < 3:
        return None
    if not all(w[0].isalpha() for w in words):
        return None
    a = "".join(w[0].upper() for w in words)
    return a if 3 <= len(a) <= 6 else None


def _classify(tags) -> tuple[str, str] | None:
    if tags.get("place") in PLACE_VALUES:
        return "osm_place", tags.get("place")
    for k, vals in LANDMARK_TAGS.items():
        v = tags.get(k)
        if v in vals:
            return "landmark", f"{k}={v}"
    if tags.get("highway") in STREET_VALUES:
        return "street", f"highway={tags.get('highway')}"
    return None


def read_osm(path: str) -> list[OsmFeature]:
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('OSM import needs pyosmium: pip install "india-address-intelligence[osm]"') from exc

    feats: list[OsmFeature] = []

    def make(osm_id: str, tags, lat: float, lon: float) -> None:
        name = tags.get("name") or tags.get("name:en")
        if not name:
            return
        cls = _classify(tags)
        if cls is None:
            return
        alts = []
        for k in NAME_KEYS:
            v = tags.get(k)
            if v and v != name:
                lang = k.split(":", 1)[1] if ":" in k else "en"
                for part in v.split(";"):
                    alts.append((part.strip(), lang))
        pc = tags.get("addr:postcode")
        pc = pc.replace(" ", "") if pc else None
        feats.append(OsmFeature(osm_id, cls[0], name.strip(), lat, lon, alts,
                                pc if pc and re.fullmatch(r"[1-9]\d{5}", pc) else None, cls[1]))

    class H(osmium.SimpleHandler):
        def node(self, n):  # noqa: N802 - pyosmium API
            if n.tags and n.location.valid():
                make(f"n{n.id}", n.tags, n.location.lat, n.location.lon)

        def way(self, w):  # noqa: N802
            if not w.tags:
                return
            pts = [(nd.lat, nd.lon) for nd in w.nodes if nd.location.valid()]
            if not pts:
                return
            if w.tags.get("highway") in STREET_VALUES:
                lat, lon = pts[len(pts) // 2]  # a point on the road, not the bbox centre
            else:
                lat = sum(p[0] for p in pts) / len(pts)
                lon = sum(p[1] for p in pts) / len(pts)
            make(f"w{w.id}", w.tags, lat, lon)

    H().apply_file(path, locations=True)
    return feats


class _NearestOffice:
    """Grid index over post-office localities for nearest-neighbour admin assignment."""

    def __init__(self, rows: list[tuple[float, float, str, str, str]]) -> None:
        self.grid: dict[tuple[int, int], list[tuple[float, float, str, str, str]]] = defaultdict(list)
        for r in rows:
            self.grid[(int(r[0] * 10), int(r[1] * 10))].append(r)

    def nearest(self, lat: float, lon: float, max_km: float = 25.0):
        ci, cj = int(lat * 10), int(lon * 10)
        best, best_d = None, max_km
        for ring in range(0, 3):
            for di in range(-ring, ring + 1):
                for dj in range(-ring, ring + 1):
                    if max(abs(di), abs(dj)) != ring:
                        continue
                    for r in self.grid.get((ci + di, cj + dj), ()):
                        d = haversine_km(lat, lon, r[0], r[1])
                        if d < best_d:
                            best, best_d = r, d
            if best is not None:
                return best, best_d
        return best, best_d


def build_osm_records(feats: list[OsmFeature], offices: list[tuple[float, float, str, str, str]],
                      pin_admin: dict[str, tuple[str, str]]) -> tuple[list[EntityRecord], list[AliasRecord], dict]:
    idx = _NearestOffice(offices)
    ents: list[EntityRecord] = []
    aliases: list[AliasRecord] = []
    stats: dict[str, int] = defaultdict(int)
    seen_streets: set[tuple[str, int, int]] = set()
    for f in feats:
        if f.kind == "street":
            k = (f.name.lower(), int(f.lat * 50), int(f.lon * 50))  # ~2 km cells: one entity per road per area
            if k in seen_streets:
                continue
            seen_streets.add(k)
        state = district = pin = None
        if f.postcode and f.postcode in pin_admin:
            pin = f.postcode
            state, district = pin_admin[f.postcode]
        else:
            near, _d = idx.nearest(f.lat, f.lon)
            if near is None:
                stats["skipped_no_nearby_office"] += 1
                continue
            state, district, pin = near[2], near[3], near[4]
        key = f"osm:{f.osm_id}"
        ents.append(EntityRecord(key=key, entity_type=f.kind, name=f.name, state=state, district=district, city=None,
                                 pincode=pin, latitude=f.lat, longitude=f.lon,
                                 source_confidence=0.75 if f.kind != "street" else 0.7,
                                 attributes={"osm_id": f.osm_id, "osm_tag": f.subtype,
                                             "pin_source": "addr:postcode" if f.postcode == pin else "nearest_post_office"}))
        stats[f.kind] += 1
        for alt, lang in f.alt_names:
            aliases.append(AliasRecord(key, alt, "alternate_name", lang, 0.85))
        if f.kind == "landmark":
            a = acronym(f.name)
            if a:
                aliases.append(AliasRecord(key, a, "acronym", "en", 0.8))
    return ents, aliases, dict(stats)


def run_osm_ingestion(session: Session, path: str) -> dict:
    t0 = time.perf_counter()
    feats = read_osm(path)
    rows = session.execute(
        select(GeoEntity.latitude, GeoEntity.longitude, GeoEntity.state_name, GeoEntity.district_name,
               GeoEntity.pincode).where(GeoEntity.entity_type == "locality", GeoEntity.latitude.is_not(None))
    ).all()
    offices = [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
    pin_admin = {r[0]: (r[1], r[2]) for r in session.execute(
        select(GeoEntity.name, GeoEntity.state_name, GeoEntity.district_name).where(GeoEntity.entity_type == "pincode"))}
    ents, aliases, stats = build_osm_records(feats, offices, pin_admin)
    src = ensure_source(session, "osm", path.rsplit("/", 1)[-1])
    n_e, n_a = write_records(session, src, ents, aliases)
    src.record_count = n_e
    summary = {"features_read": len(feats), "entities": n_e, "aliases": n_a, **stats,
               "seconds": round(time.perf_counter() - t0, 1)}
    log_event(log, "osm_ingestion_complete", **summary)
    return summary
