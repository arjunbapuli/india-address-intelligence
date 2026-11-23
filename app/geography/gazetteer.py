"""In-memory gazetteer: fast lookup structures over the geographic database.

The database is the system of record; at startup we load every active entity
and alias into compact in-memory indices so a resolution never needs a DB
round-trip. ~186k entities use roughly 150-250 MB of RAM.

Indices:
  * exact:     compact key ("hsrlayout") -> [(place_id, kind, confidence)]
  * phonetic:  Indic phonetic skeleton ("hsrlt") -> [place_id]
  * fuzzy:     list of unique compact keys searched with rapidfuzz
  * hierarchy: pincode -> localities, (state, district) -> localities, cities -> districts
"""

from __future__ import annotations

import bisect
import math
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from app.core.logging import get_logger, log_event
from app.intelligence.phonetics.indic import indic_phonetic_key
from app.intelligence.text import compact_key

log = get_logger(__name__)

ADMIN_TYPES = ("state", "district", "city")
PLACE_TYPES = ("locality", "landmark", "street", "osm_place")


@dataclass(slots=True)
class Place:
    id: int
    type: str
    name: str
    state: str | None
    district: str | None
    city: str | None
    pincode: str | None
    lat: float | None
    lon: float | None
    source: str
    source_confidence: float
    office_type: str | None = None
    delivery: bool | None = None
    coord_quality: str | None = None


@dataclass(slots=True)
class KeyHit:
    place_id: int
    kind: str  # "name" or "alias:<alias_type>"
    confidence: float


@dataclass
class CityInfo:
    place_id: int
    name: str
    state: str
    districts: list[str]
    infer_districts: list[str]
    tier: int


@dataclass
class GazetteerStats:
    entities: dict[str, int] = field(default_factory=dict)
    aliases: int = 0
    states_covered: int = 0
    districts_covered: int = 0
    pincodes: int = 0
    localities_with_coordinates: int = 0
    sources: dict[str, dict] = field(default_factory=dict)
    load_seconds: float = 0.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


class Gazetteer:
    def __init__(self) -> None:
        self.places: dict[int, Place] = {}
        self.exact: dict[str, list[KeyHit]] = defaultdict(list)
        self.phonetic: dict[str, list[tuple[int, str]]] = defaultdict(list)  # key -> [(place_id, matched name key)]
        self.key_list: list[str] = []
        self._key_set: set[str] = set()
        self.sorted_keys: list[str] = []
        self.pin_localities: dict[str, list[int]] = defaultdict(list)
        self.pin_place: dict[str, int] = {}
        self.district_place: dict[tuple[str, str], int] = {}
        self.district_localities: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.state_place: dict[str, int] = {}
        self.cities: dict[int, CityInfo] = {}
        self.city_by_district: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.infer_city_for_district: dict[tuple[str, str], int] = {}
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.stats = GazetteerStats()
        self._names_by_key_type: dict[str, set[str]] = defaultdict(set)

    # ------------------------------------------------------------------ build
    def add_place(self, p: Place, attributes: dict | None = None) -> None:
        self.places[p.id] = p
        attributes = attributes or {}
        self._index_key(p.name, KeyHit(p.id, "name", 1.0))
        if p.type == "pincode":
            self.pin_place[p.name] = p.id
        elif p.type == "state":
            self.state_place[p.name] = p.id
        elif p.type == "district" and p.state:
            self.district_place[(p.state, p.name)] = p.id
        elif p.type == "city" and p.state:
            info = CityInfo(p.id, p.name, p.state, list(attributes.get("districts", [])),
                            list(attributes.get("infer_districts", [])), int(attributes.get("tier", 2)))
            self.cities[p.id] = info
            for d in info.districts:
                self.city_by_district[(p.state, d)].append(p.id)
        if p.type in PLACE_TYPES:
            if p.pincode:
                self.pin_localities[p.pincode].append(p.id)
            if p.state and p.district:
                self.district_localities[(p.state, p.district)].append(p.id)
            if p.lat is not None and p.lon is not None:
                self.grid[(int(p.lat * 10), int(p.lon * 10))].append(p.id)

    def add_alias(self, place_id: int, alias: str, alias_type: str, confidence: float) -> None:
        if place_id in self.places:
            self._index_key(alias, KeyHit(place_id, f"alias:{alias_type}", confidence))

    def _index_key(self, text: str, hit: KeyHit) -> None:
        if any(ord(ch) > 0x08FF for ch in text):
            # Indic-script alias ("சென்னை"): index its transliteration, the same form the input normaliser produces
            from app.intelligence.normalization.transliterate import to_latin

            text = to_latin(text)[0]
        key = compact_key(text)
        if not key:
            return
        bucket = self.exact[key]
        if not any(h.place_id == hit.place_id for h in bucket):
            bucket.append(hit)
        if key not in self._key_set:
            self._key_set.add(key)
            self.key_list.append(key)
        if len(key) >= 3 and not key.isdigit():
            pk = indic_phonetic_key(text)
            entry = (hit.place_id, key)
            if pk and entry not in self.phonetic[pk][-3:]:
                self.phonetic[pk].append(entry)

    def finalize(self) -> None:
        self.sorted_keys = sorted(self._key_set)
        for cid, info in self.cities.items():
            for d in info.infer_districts:
                self.infer_city_for_district.setdefault((info.state, d), cid)
        counts: dict[str, int] = defaultdict(int)
        for p in self.places.values():
            counts[p.type] += 1
        self.stats.entities = dict(counts)
        self.stats.states_covered = len(self.state_place)
        self.stats.districts_covered = len(self.district_place)
        self.stats.pincodes = len(self.pin_place)
        self.stats.localities_with_coordinates = sum(
            1 for p in self.places.values() if p.type == "locality" and p.lat is not None
        )

    # ------------------------------------------------------------- loading
    @classmethod
    def from_records(cls, entities: Iterable[dict], aliases: Iterable[dict], sources: dict[str, dict] | None = None
                     ) -> Gazetteer:
        """Build from plain dicts (used by the DB loader and by tests)."""
        g = cls()
        t0 = time.perf_counter()
        for e in entities:
            attrs = e.get("attributes") or {}
            g.add_place(
                Place(
                    id=int(e["id"]), type=e["entity_type"], name=e["name"], state=e.get("state_name"),
                    district=e.get("district_name"), city=e.get("city_name"), pincode=e.get("pincode"),
                    lat=e.get("latitude"), lon=e.get("longitude"), source=e.get("source_key") or "unknown",
                    source_confidence=float(e.get("source_confidence") or 0.8),
                    office_type=attrs.get("office_type"), delivery=attrs.get("delivery"),
                    coord_quality=attrs.get("coord_quality"),
                ),
                attrs,
            )
        n_alias = 0
        for a in aliases:
            g.add_alias(int(a["entity_id"]), a["alias"], a["alias_type"], float(a.get("confidence") or 0.9))
            n_alias += 1
        g.stats.aliases = n_alias
        g.stats.sources = sources or {}
        g.finalize()
        g.stats.load_seconds = round(time.perf_counter() - t0, 2)
        return g

    @classmethod
    def load_from_db(cls) -> Gazetteer:
        from sqlalchemy import select

        from app.database.models import DataSource, GeoAlias, GeoEntity
        from app.database.session import session_scope

        t0 = time.perf_counter()
        with session_scope() as s:
            srcs = {r.id: r for r in s.scalars(select(DataSource)).all()}
            source_meta = {
                r.key: {"name": r.name, "license": r.license, "attribution": r.attribution, "url": r.url,
                        "version": r.version, "records": r.record_count,
                        "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None}
                for r in srcs.values()
            }
            src_key = {i: r.key for i, r in srcs.items()}
            cols = (GeoEntity.id, GeoEntity.entity_type, GeoEntity.name, GeoEntity.state_name,
                    GeoEntity.district_name, GeoEntity.city_name, GeoEntity.pincode, GeoEntity.latitude,
                    GeoEntity.longitude, GeoEntity.source_id, GeoEntity.source_confidence, GeoEntity.attributes)
            ents = [
                {"id": r[0], "entity_type": r[1], "name": r[2], "state_name": r[3], "district_name": r[4],
                 "city_name": r[5], "pincode": r[6], "latitude": r[7], "longitude": r[8],
                 "source_key": src_key.get(r[9]), "source_confidence": r[10], "attributes": r[11]}
                for r in s.execute(select(*cols).where(GeoEntity.is_active.is_(True))).all()
            ]
            als = [
                {"entity_id": r[0], "alias": r[1], "alias_type": r[2], "confidence": r[3]}
                for r in s.execute(select(GeoAlias.entity_id, GeoAlias.alias, GeoAlias.alias_type,
                                          GeoAlias.confidence)).all()
            ]
        g = cls.from_records(ents, als, source_meta)
        g.stats.load_seconds = round(time.perf_counter() - t0, 2)
        log_event(log, "gazetteer_loaded", entities=len(g.places), aliases=len(als), seconds=g.stats.load_seconds)
        return g

    # ------------------------------------------------------------- queries
    def lookup_exact(self, text: str) -> list[KeyHit]:
        return list(self.exact.get(compact_key(text), ()))

    def lookup_phonetic(self, text: str) -> list[int]:
        return [pid for pid, _ in self.lookup_phonetic_keys(text)]

    def lookup_phonetic_keys(self, text: str) -> list[tuple[int, str]]:
        """[(place_id, compact key of the name/alias that produced the phonetic match)]"""
        pk = indic_phonetic_key(text)
        return list(self.phonetic.get(pk, ())) if pk else []

    def fuzzy_keys(self, text: str, limit: int = 15, score_cutoff: float = 80.0,
                   choices: list[str] | None = None) -> list[tuple[str, float]]:
        key = compact_key(text)
        if len(key) < 4:
            return []
        pool = choices if choices is not None else self.key_list
        res = process.extract(key, pool, scorer=fuzz.ratio, limit=limit, score_cutoff=score_cutoff)
        return [(k, float(score)) for k, score, _ in res]

    def prefix_keys(self, text: str, limit: int = 50) -> list[str]:
        key = compact_key(text)
        if not key:
            return []
        i = bisect.bisect_left(self.sorted_keys, key)
        out = []
        while i < len(self.sorted_keys) and self.sorted_keys[i].startswith(key) and len(out) < limit:
            out.append(self.sorted_keys[i])
            i += 1
        return out

    def keys_for_places(self, place_ids: Iterable[int]) -> dict[str, list[int]]:
        """Compact names for a subset of places (used for scoped fuzzy search)."""
        out: dict[str, list[int]] = defaultdict(list)
        for pid in place_ids:
            p = self.places.get(pid)
            if p:
                out[compact_key(p.name)].append(pid)
        return out

    def localities_in_districts(self, districts: Iterable[tuple[str, str]]) -> list[int]:
        out: list[int] = []
        for d in districts:
            out.extend(self.district_localities.get(d, ()))
        return out

    def city_for_locality(self, p: Place) -> CityInfo | None:
        if not (p.state and p.district):
            return None
        cid = self.infer_city_for_district.get((p.state, p.district))
        return self.cities.get(cid) if cid is not None else None

    def nearby(self, lat: float, lon: float, radius_km: float = 2.0, types: tuple[str, ...] | None = None
               ) -> list[tuple[Place, float]]:
        cells = max(1, int(radius_km / 11) + 1)
        ci, cj = int(lat * 10), int(lon * 10)
        out = []
        for di in range(-cells, cells + 1):
            for dj in range(-cells, cells + 1):
                for pid in self.grid.get((ci + di, cj + dj), ()):
                    p = self.places[pid]
                    if types and p.type not in types:
                        continue
                    if p.lat is None or p.lon is None:
                        continue
                    d = haversine_km(lat, lon, p.lat, p.lon)
                    if d <= radius_km:
                        out.append((p, d))
        out.sort(key=lambda x: x[1])
        return out

    def coverage(self) -> dict:
        return {
            "entities_by_type": self.stats.entities,
            "aliases": self.stats.aliases,
            "states_and_uts": self.stats.states_covered,
            "districts": self.stats.districts_covered,
            "pincodes": self.stats.pincodes,
            "localities_with_coordinates": self.stats.localities_with_coordinates,
            "sources": self.stats.sources,
        }


_gazetteer: Gazetteer | None = None


def get_gazetteer() -> Gazetteer | None:
    return _gazetteer


def set_gazetteer(g: Gazetteer | None) -> None:
    global _gazetteer
    _gazetteer = g
