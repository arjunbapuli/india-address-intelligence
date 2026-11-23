"""Stage 6: transparent weighted ranking of candidate places.

Each candidate gets a feature vector in [0, 1]; the score is the weighted
average. Weights are configuration (see Settings.ranking_weights) so they can
be tuned against the evaluation set without code changes. When there is no
evidence for a feature (e.g. the user gave no PIN), it takes a neutral value,
so a candidate supported by more independent evidence scores higher.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.geography.gazetteer import Gazetteer, Place, haversine_km
from app.intelligence.matching.matcher import MATCH_TYPE_SCORE, Candidate
from app.intelligence.text import compact_key

NEUTRAL = 0.6


@dataclass
class AdminContext:
    """Administrative evidence from the input and request hints."""

    states: set[str] = field(default_factory=set)
    districts: set[tuple[str, str]] = field(default_factory=set)
    city_ids: set[int] = field(default_factory=set)
    city_districts: set[tuple[str, str]] = field(default_factory=set)
    pincode: str | None = None
    pin_valid: bool = False
    pin_place: Place | None = None
    pin_districts: set[tuple[str, str]] = field(default_factory=set)
    near_lat: float | None = None
    near_lon: float | None = None

    @property
    def scope_districts(self) -> set[tuple[str, str]]:
        """Districts to scope fuzzy search: the most specific evidence available."""
        if self.pin_districts:
            return self.pin_districts
        if self.districts:
            return self.districts
        if self.city_districts:
            return self.city_districts
        return set()


@dataclass
class ScoredCandidate:
    place: Place
    candidate: Candidate
    mention_text: str
    features: dict[str, float]
    score: float


def features_for(g: Gazetteer, p: Place, c: Candidate, ctx: AdminContext, landmark_support: float | None,
                 ) -> dict[str, float]:
    f: dict[str, float] = {}
    f["name_similarity"] = c.similarity
    f["match_type"] = MATCH_TYPE_SCORE.get(c.match_type, 0.5)

    # PIN consistency
    if ctx.pincode and ctx.pin_valid:
        if p.pincode == ctx.pincode or pin_in_name_family(g, ctx.pincode, p):
            f["pin_consistency"] = 1.0
        elif (p.state, p.district) in ctx.pin_districts:
            f["pin_consistency"] = 0.45  # same district, different delivery area
        else:
            f["pin_consistency"] = 0.0
    elif ctx.pincode and not ctx.pin_valid:
        f["pin_consistency"] = 0.4  # PIN given but not in India Post data
    else:
        f["pin_consistency"] = NEUTRAL

    # City / district consistency
    if ctx.city_districts or ctx.districts:
        ok = (p.state, p.district) in ctx.city_districts or (p.state, p.district) in ctx.districts
        f["city_consistency"] = 1.0 if ok else 0.05
    else:
        f["city_consistency"] = NEUTRAL

    if ctx.states:
        f["state_consistency"] = 1.0 if p.state in ctx.states else 0.0
    elif ctx.city_districts or ctx.districts or (ctx.pin_valid and ctx.pin_place is not None):
        # A mentioned city/district/PIN implies its state
        implied = {s for s, _ in ctx.city_districts | ctx.districts}
        if ctx.pin_valid and ctx.pin_place is not None and ctx.pin_place.state:
            implied.add(ctx.pin_place.state)
        f["state_consistency"] = 1.0 if p.state in implied else 0.0
    else:
        f["state_consistency"] = NEUTRAL

    # Landmark and proximity evidence is rarely available; the features are only
    # included when there is evidence, so their absence neither helps nor hurts.
    if landmark_support is not None:
        f["landmark"] = landmark_support

    if ctx.near_lat is not None and ctx.near_lon is not None and p.lat is not None and p.lon is not None:
        d = haversine_km(ctx.near_lat, ctx.near_lon, p.lat, p.lon)
        f["proximity"] = max(0.0, 1.0 - d / 25.0)

    f["source_confidence"] = p.source_confidence
    return f


def pin_in_name_family(g: Gazetteer, pin: str, p: Place) -> bool:
    """True if the PIN serves a post office whose name extends this locality's name
    ('Koramangala' and PIN 560095 of 'Koramangala 6th Block'): large localities span several PINs."""
    key = compact_key(p.name)
    if len(key) < 4:
        return False
    for lid in g.pin_localities.get(pin, ()):
        o = g.places[lid]
        if o.district == p.district and o.state == p.state:
            ok = compact_key(o.name)
            if ok.startswith(key) or key.startswith(ok):
                return True
    return False


def score(features: dict[str, float], weights: dict[str, float]) -> float:
    total_w = sum(weights.get(k, 0.0) for k in features)
    if total_w <= 0:
        return 0.0
    return sum(features[k] * weights.get(k, 0.0) for k in features) / total_w


def rank(g: Gazetteer, scored: list[ScoredCandidate]) -> list[ScoredCandidate]:
    # Stable tie-breakers: delivery offices and sub/head offices before branch offices
    def tiebreak(s: ScoredCandidate) -> tuple:
        p = s.place
        city = g.city_for_locality(p)
        return (
            -round(s.score, 4),
            city.tier if city else 9,  # urban prior only breaks exact ties
            0 if p.delivery else 1,
            {"HO": 0, "PO": 1, "BO": 2}.get(p.office_type or "", 3),
            p.name,
        )

    return sorted(scored, key=tiebreak)


def distinct_places(items: list[ScoredCandidate]) -> list[ScoredCandidate]:
    """Collapse candidates that denote the same place (same name and district)."""
    seen = set()
    out = []
    for s in items:
        k = (s.place.name.lower(), s.place.state, s.place.district, s.place.type)
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out
