"""Stage 2: deterministic component extraction.

Works on the normalised token stream. Produces:
  * structured components that are *not* in any open dataset (house number,
    flat, floor, building, cross/main road, sector, block, phase ...);
  * landmark mentions ("near X", "X ke paas");
  * free token spans that should be matched against the gazetteer
    (locality / city / district / state).

Before slot extraction, multi-word exact gazetteer matches are "claimed" so
place names containing slot words ("Koramangala 6th Block",
"Bannerghatta Road", "JP Nagar 7th Phase") are not broken apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.geography.gazetteer import Gazetteer
from app.intelligence.normalization.vocabulary import (
    GENERIC_LANDMARK_WORDS,
    LANDMARK_POSTPOSITIONS,
    LANDMARK_PREPOSITIONS,
)
from app.intelligence.text import is_generic_only, ordinal, smart_title

ORD = re.compile(r"^(\d{1,3})(st|nd|rd|th)$")
NUM = re.compile(r"^\d{1,4}[a-z]?$")
PIN = re.compile(r"^[1-9]\d{5}$")
UNIT_ID = re.compile(r"^(?:[a-z]{1,2}-?\d{1,4}[a-z]?|\d{1,4}-?[a-z]{1,2}|\d{1,5}(?:/\d{1,4}){0,2}[a-z]?|\d{1,4}-\d{1,4})$")
STREET_SUFFIX = {"road", "street", "marg", "path", "avenue", "highway", "lane", "salai", "rasta", "galli", "gali"}
BUILDING_SUFFIX = {
    "apartment", "apartments", "residency", "residences", "residence", "towers", "tower", "heights", "society",
    "complex", "villas", "villa", "homes", "mansion", "plaza", "chambers", "bhavan", "bhawan", "niwas", "nivas",
    "sadan", "kunj", "arcade", "court", "manor", "enclave", "greens", "meadows", "chs", "housing", "flats", "pg",
    "hostel", "mall",
}
SLOT_STARTERS = {"house", "flat", "plot", "shop", "door", "no", "floor", "sector", "phase", "block", "stage",
                 "pocket", "ward", "gali", "lane", "care"}
FLOOR_WORDS = {"ground": "Ground Floor", "basement": "Basement", "terrace": "Terrace", "first": "1st Floor",
               "top": "Top Floor", "upper": "Upper Floor", "lower": "Lower Ground Floor"}


@dataclass
class Span:
    start: int
    end: int  # exclusive

    def tokens(self, toks: list[str]) -> list[str]:
        return toks[self.start:self.end]


@dataclass
class LandmarkMention:
    text: str
    relation: str  # near / opposite / behind / beside
    span: Span
    generic: bool
    postposition: bool = False  # Hinglish order: "<landmark> ke paas"


@dataclass
class Extraction:
    tokens: list[str]
    components: dict[str, str] = field(default_factory=dict)
    streets: list[str] = field(default_factory=list)
    landmarks: list[LandmarkMention] = field(default_factory=list)
    free_spans: list[Span] = field(default_factory=list)
    claimed_spans: list[Span] = field(default_factory=list)
    pin_candidates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _segments(tokens: list[str]) -> list[Span]:
    segs, start = [], 0
    for i, t in enumerate(tokens):
        if t == ",":
            if i > start:
                segs.append(Span(start, i))
            start = i + 1
    if start < len(tokens):
        segs.append(Span(start, len(tokens)))
    return segs


def _fmt_unit_label(tok: str) -> str:
    return tok.upper() if re.search(r"[a-z]", tok) and len(tok) <= 6 else tok


def _ordinal_or_none(tok: str) -> str | None:
    m = ORD.match(tok)
    if m:
        return ordinal(int(m.group(1)))
    return None


def claim_known_places(tokens: list[str], gaz: Gazetteer | None, used: list[bool], max_n: int = 6) -> list[Span]:
    """Pass A: claim multi-token exact gazetteer names (longest first) that contain a slot word."""
    if gaz is None:
        return []
    claimed: list[Span] = []
    n = len(tokens)
    for size in range(min(max_n, n), 1, -1):
        for i in range(0, n - size + 1):
            j = i + size
            if any(used[i:j]) or "," in tokens[i:j] or any(t.startswith("_") for t in tokens[i:j]):
                continue
            words = tokens[i:j]
            if is_generic_only(words):
                continue
            if words[0] in LANDMARK_PREPOSITIONS or words[0] in ("house", "flat", "no", "plot"):
                continue
            hits = gaz.lookup_exact(" ".join(words))
            if any(gaz.places[h.place_id].type in ("locality", "osm_place", "city", "district", "state",
                                                    "street") for h in hits):
                claimed.append(Span(i, j))
                for k in range(i, j):
                    used[k] = True
    return claimed


def extract(tokens: list[str], gaz: Gazetteer | None = None) -> Extraction:
    ex = Extraction(tokens=tokens)
    n = len(tokens)
    used = [t == "," for t in tokens]

    # PIN codes first (6 digits, or "560 034")
    for i, t in enumerate(tokens):
        if PIN.match(t):
            ex.pin_candidates.append(t)
            used[i] = True
        elif re.fullmatch(r"[1-9]\d{2}", t) and i + 1 < n and re.fullmatch(r"\d{3}", tokens[i + 1]):
            ex.pin_candidates.append(t + tokens[i + 1])
            used[i] = used[i + 1] = True

    ex.claimed_spans = claim_known_places(tokens, gaz, used)

    def take(i: int, j: int) -> None:
        for k in range(i, j):
            used[k] = True

    def free(i: int) -> bool:
        return 0 <= i < n and not used[i]

    i = 0
    while i < n:
        if not free(i):
            i += 1
            continue
        t = tokens[i]
        nx = tokens[i + 1] if i + 1 < n else ""
        nx2 = tokens[i + 2] if i + 2 < n else ""

        # care of <name> : drop the person's name, it is not part of the address
        if t == "care" and nx == "of":
            j = i + 2
            while j < n and tokens[j] != "," and free(j):
                j += 1
            ex.notes.append("care_of_removed")
            take(i, j)
            i = j
            continue
        # house no X / flat no X / plot no X / shop no X / gali no X
        if t in ("house", "flat", "plot", "shop", "gali") and nx == "no" and free(i + 2) and nx2 not in (",", ""):
            val = nx2
            j = i + 3
            if j < n and free(j) and re.fullmatch(r"[a-z]", tokens[j]):
                val += tokens[j]
                j += 1
            comp = {"house": "house_number", "flat": "unit", "plot": "plot", "shop": "unit", "gali": "lane"}[t]
            ex.components[comp] = f"Gali No. {val}" if t == "gali" else _fmt_unit_label(val)
            take(i, j)
            i = j
            continue
        # Hyderabad/Chennai style numbered roads: "road no 12" -> "Road No. 12"
        if t in ("road", "street", "lane", "cross", "main") and nx == "no" and free(i + 2) and re.fullmatch(r"\d{1,3}[a-z]?", nx2):
            label = f"{t.title()} No. {nx2.upper()}"
            if t == "cross":
                ex.components.setdefault("cross_road", label)
            elif t == "lane":
                ex.components.setdefault("lane", label)
            else:
                ex.streets.append(label)
            take(i, i + 3)
            i += 3
            continue
        if t == "no" and free(i + 1) and UNIT_ID.match(nx):
            ex.components.setdefault("house_number", _fmt_unit_label(nx))
            take(i, i + 2)
            i += 2
            continue
        # floors: "3rd floor", "floor 3", "ground floor"
        if nx == "floor" and (ORD.match(t) or t in FLOOR_WORDS):
            ex.components["floor"] = FLOOR_WORDS.get(t) or f"{_ordinal_or_none(t)} Floor"
            take(i, i + 2)
            i += 2
            continue
        if t == "floor" and free(i + 1) and re.fullmatch(r"\d{1,2}", nx):
            ex.components["floor"] = f"{ordinal(int(nx))} Floor"
            take(i, i + 2)
            i += 2
            continue
        # Nth main / Nth cross / Nth stage / Nth block / Nth phase / Nth street / Nth lane / Nth sector
        o = _ordinal_or_none(t)
        if o and nx in ("main", "cross", "stage", "block", "phase", "street", "lane", "avenue", "sector", "road", "gali"):
            j = i + 2
            if nx in ("main", "cross") and j < n and tokens[j] == "road" and free(j):
                j += 1
            label = {"main": "Main", "cross": "Cross", "stage": "Stage", "block": "Block", "phase": "Phase",
                     "street": "Street", "lane": "Lane", "avenue": "Avenue", "sector": "Sector", "road": "Road",
                     "gali": "Gali"}[nx]
            val = f"{o} {label}"
            if nx in ("main", "street", "avenue", "road"):
                ex.streets.append(val)
            elif nx == "cross":
                ex.components.setdefault("cross_road", val)
            elif nx in ("lane", "gali"):
                ex.components.setdefault("lane", val)
            else:
                ex.components.setdefault(nx, val)
            take(i, j)
            i = j
            continue
        # sector 2 / phase 1 / block a / stage 2 / pocket c / ward 12 / lane 4 / street 5
        if t in ("sector", "phase", "block", "stage", "pocket", "ward", "lane", "street") and free(i + 1) and (
            re.fullmatch(r"\d{1,3}[a-z]?", nx) or (re.fullmatch(r"[a-z]{1,2}", nx) and t in ("block", "pocket", "sector"))
        ):
            label = t.title()
            val = f"{label} {nx.upper() if nx.isalpha() else nx.upper() if re.search('[a-z]', nx) else nx}"
            comp = {"lane": "lane", "street": "street"}.get(t, t)
            if comp == "street":
                ex.streets.append(val)
            else:
                ex.components.setdefault(comp, val)
            take(i, i + 2)
            i += 2
            continue
        # "a block" / "c-block"
        if nx == "block" and re.fullmatch(r"[a-z]{1,2}", t):
            ex.components.setdefault("block", f"{t.upper()} Block")
            take(i, i + 2)
            i += 2
            continue
        i += 1

    # Landmarks: prepositions (landmark follows) and Hinglish postpositions (landmark precedes)
    for seg in _segments(tokens):
        k = seg.start
        while k < seg.end:
            t = tokens[k]
            if t in LANDMARK_PREPOSITIONS and free(k):
                j = k + 1
                while j < seg.end and free(j) and tokens[j] not in LANDMARK_PREPOSITIONS and not tokens[j].startswith("_"):
                    j += 1
                if j > k + 1:
                    ex.landmarks.append(_landmark(tokens, Span(k + 1, j), t))
                take(k, j)
                k = j
                continue
            if t in LANDMARK_POSTPOSITIONS:
                j = k - 1
                while j >= seg.start and free(j) and tokens[j] not in LANDMARK_PREPOSITIONS:
                    j -= 1
                if j + 1 < k:
                    ex.landmarks.append(_landmark(tokens, Span(j + 1, k), LANDMARK_POSTPOSITIONS[t], post=True))
                take(j + 1, k + 1)
            k += 1

    # Named streets and buildings inside remaining free text
    for seg in _segments(tokens):
        k = seg.start
        run_start = None
        while k <= seg.end:
            is_free = k < seg.end and free(k)
            if is_free and run_start is None:
                run_start = k
            if (not is_free) and run_start is not None:
                _named_slots(tokens, Span(run_start, k), ex, take, gaz)
                run_start = None
            k += 1

    # Leading bare number in the first segment = house/door number ("25, 3rd cross", "45 brigade road")
    segs = _segments(tokens)
    if segs and "house_number" not in ex.components:
        s0 = segs[0]
        if free(s0.start) and UNIT_ID.match(tokens[s0.start]) and not PIN.match(tokens[s0.start]):
            ex.components["house_number"] = _fmt_unit_label(tokens[s0.start])
            take(s0.start, s0.start + 1)

    # Remaining free tokens become spans for gazetteer matching
    k = 0
    while k < n:
        if free(k) and not tokens[k].startswith("_"):
            j = k
            while j < n and free(j) and not tokens[j].startswith("_"):
                j += 1
            ex.free_spans.append(Span(k, j))
            k = j
        else:
            k += 1
    if ex.streets:
        ex.components["street"] = ", ".join(dict.fromkeys(ex.streets))
    return ex


def _landmark(tokens: list[str], span: Span, relation: str, post: bool = False) -> LandmarkMention:
    words = span.tokens(tokens)
    content = [w for w in words if w not in ("the", "a", "an")]
    generic = all(w in GENERIC_LANDMARK_WORDS for w in content) if content else True
    return LandmarkMention(text=smart_title(" ".join(content)), relation=relation, span=span, generic=generic,
                           postposition=post)


def landmark_from_span(tokens: list[str], span: Span, relation: str, post: bool) -> LandmarkMention:
    return _landmark(tokens, span, relation, post)


def _is_known_place_word(gaz: Gazetteer | None, word: str) -> bool:
    if gaz is None or len(word) < 3:
        return False
    return any(gaz.places[h.place_id].type in ("locality", "city", "district", "state")
               for h in gaz.lookup_exact(word))


def _named_slots(tokens: list[str], run: Span, ex: Extraction, take, gaz: Gazetteer | None = None) -> None:
    """Within a free run, detect '<Name> Road' streets and '<Name> Apartments' buildings."""
    words = run.tokens(tokens)
    for idx, w in enumerate(words):
        if idx == 0:
            continue
        if w in STREET_SUFFIX or (w == "main" and idx + 1 < len(words) and words[idx + 1] == "road"):
            end = idx + 2 if w == "main" else idx + 1
            start = max(0, idx - 2)
            # Don't swallow a leading house number or a known locality/city into the street name
            while start < idx - 1 and (UNIT_ID.match(words[start]) or _is_known_place_word(gaz, words[start])):
                start += 1
            if start < idx and UNIT_ID.match(words[start]):
                start += 1
            if start >= idx:
                continue
            name_words = words[start:end]
            if is_generic_only(name_words[:-1]):
                continue
            ex.streets.append(smart_title(" ".join(name_words)))
            take(run.start + start, run.start + end)
            _rest_slots(tokens, Span(run.start + end, run.end), ex, take, gaz)
            if start > 0:
                _rest_slots(tokens, Span(run.start, run.start + start), ex, take, gaz)
            return
        if w in BUILDING_SUFFIX:
            start = max(0, idx - 3)
            while start < idx - 1 and _is_known_place_word(gaz, words[start]):
                start += 1
            while start < idx and UNIT_ID.match(words[start]):
                start += 1
            if start >= idx:
                continue
            ex.components.setdefault("building", smart_title(" ".join(words[start:idx + 1])))
            take(run.start + start, run.start + idx + 1)
            _rest_slots(tokens, Span(run.start + idx + 1, run.end), ex, take, gaz)
            if start > 0:
                _rest_slots(tokens, Span(run.start, run.start + start), ex, take, gaz)
            return


def _rest_slots(tokens: list[str], run: Span, ex: Extraction, take, gaz: Gazetteer | None) -> None:
    if run.end - run.start >= 2:
        _named_slots(tokens, run, ex, take, gaz)
