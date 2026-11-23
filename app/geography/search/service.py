"""Autocomplete / candidate search over the gazetteer (GET /v1/address/search)."""

from __future__ import annotations

import re

from pydantic import BaseModel
from rapidfuzz import fuzz

from app.geography.gazetteer import Gazetteer, Place
from app.intelligence.normalization.normalizer import normalize
from app.intelligence.phonetics.indic import phonetic_similarity
from app.intelligence.text import compact_key

TYPE_PRIORITY = {"state": 0, "city": 1, "district": 2, "pincode": 3, "locality": 4, "osm_place": 4, "street": 5,
                 "landmark": 5}


class SearchHit(BaseModel):
    id: int
    type: str
    name: str
    match_type: str
    score: float
    matched_alias: str | None = None
    locality: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    postal_code: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    source: str | None = None
    label: str


def _label(g: Gazetteer, p: Place) -> str:
    if p.type == "state":
        return f"{p.name}, India"
    if p.type == "district":
        return f"{p.name} District, {p.state}"
    if p.type == "city":
        return f"{p.name}, {p.state}"
    if p.type == "pincode":
        return f"{p.name} - {p.district}, {p.state}"
    city = g.city_for_locality(p)
    where = city.name if city else p.district
    return ", ".join(x for x in (p.name, where, p.state) if x) + (f" {p.pincode}" if p.pincode else "")


def search(g: Gazetteer, q: str, types: list[str] | None = None, state: str | None = None,
           district: str | None = None, limit: int = 10) -> list[SearchHit]:
    q = q.strip()
    if not q:
        return []
    want = set(types) if types else None
    scored: dict[int, tuple[float, str, str | None]] = {}

    def consider(pid: int, s: float, mt: str, alias: str | None = None) -> None:
        p = g.places.get(pid)
        if p is None or (want and p.type not in want):
            return
        if state and (p.state or "").lower() != state.lower():
            return
        if district and (p.district or "").lower() != district.lower():
            return
        if pid not in scored or scored[pid][0] < s:
            scored[pid] = (s, mt, alias)

    digits = re.sub(r"\D", "", q)
    if digits and len(digits) == len(q.replace(" ", "")):
        # PIN prefix search: "5600" -> all PINs starting with 5600
        for pin, pid in g.pin_place.items():
            if pin.startswith(digits):
                consider(pid, 1.0 if pin == digits else 0.8, "exact" if pin == digits else "prefix")
        if len(digits) == 6:
            for lid in g.pin_localities.get(digits, []):
                consider(lid, 0.95, "pincode")
    else:
        words = [t for t in normalize(q).tokens if t != ","]
        # "anna nagar chennai": a trailing city/district/state narrows the search instead of being matched
        if len(words) > 1 and not (state or district):
            for k in (2, 1):
                if len(words) <= k:
                    continue
                tail = " ".join(words[-k:])
                admin = [g.places[h.place_id] for h in g.lookup_exact(tail)
                         if g.places[h.place_id].type in ("city", "district", "state")]
                if admin:
                    scope_states = {p.state for p in admin}
                    scope_districts = set()
                    for p in admin:
                        if p.type == "city" and p.id in g.cities:
                            scope_districts.update(g.cities[p.id].districts)
                        elif p.type == "district":
                            scope_districts.add(p.name)
                    inner = search(g, " ".join(words[:-k]), types, None, None, limit * 5)
                    inner = [h for h in inner if h.state in scope_states and (
                        not scope_districts or h.district in scope_districts or h.type in ("state", "city"))]
                    if inner:
                        return inner[:limit]
        text = " ".join(words)
        key = compact_key(text)
        for h in g.lookup_exact(text):
            admin = g.places[h.place_id].type in ("state", "city", "district")
            consider(h.place_id, 1.0 if (h.kind == "name" or admin) else 0.97,
                     "exact" if h.kind == "name" else "alias", text if h.kind != "name" else None)
        for k in g.prefix_keys(text, limit=200):
            for h in g.exact.get(k, ()):
                bonus = len(key) / max(len(k), 1)
                consider(h.place_id, 0.7 + 0.2 * bonus, "prefix")
        for pid in g.lookup_phonetic(text):
            p = g.places[pid]
            consider(pid, 0.55 * phonetic_similarity(text, p.name) + 0.35 * fuzz.ratio(key, compact_key(p.name)) / 100,
                     "phonetic")
        for k, sc in g.fuzzy_keys(text, limit=25, score_cutoff=75):
            for h in g.exact.get(k, ()):
                consider(h.place_id, 0.8 * sc / 100.0, "fuzzy")
        # Partial-token match for multi-word queries ("anna nagar chennai")
        if len(text.split()) > 1:
            for pid, (s, mt, al) in list(scored.items()):
                p = g.places[pid]
                extra = fuzz.token_set_ratio(text, f"{p.name} {p.district or ''} {p.state or ''}") / 100.0
                scored[pid] = (max(s, 0.85 * extra), mt, al)

    hits = []
    for pid, (s, mt, alias) in scored.items():
        p = g.places[pid]
        city = g.city_for_locality(p) if p.type in ("locality", "osm_place", "landmark", "street") else None
        hits.append(SearchHit(
            id=p.id, type=p.type, name=p.name, match_type=mt, score=round(min(s, 1.0), 3), matched_alias=alias,
            locality=p.name if p.type in ("locality", "osm_place") else None,
            city=p.name if p.type == "city" else (city.name if city else None), district=p.district, state=p.state,
            postal_code=p.pincode, latitude=p.lat, longitude=p.lon, source=p.source, label=_label(g, p)))
    def prior(h: SearchHit) -> float:
        # Urban prior for autocomplete: localities in curated cities, then sub/head post offices
        p = g.places[h.id]
        bonus = 0.0
        if h.city:
            bonus += 0.03
        if p.office_type in ("PO", "HO"):
            bonus += 0.01
        return bonus

    hits.sort(key=lambda h: (-(h.score + prior(h)), TYPE_PRIORITY.get(h.type, 9), h.name))
    return hits[:limit]
