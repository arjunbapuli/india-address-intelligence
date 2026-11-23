"""Small, dependency-free text helpers shared across the pipeline."""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_WS = re.compile(r"\s+")

# Words that are part of many place names but carry little identifying signal.
GENERIC_PLACE_WORDS = frozenset(
    {
        "nagar", "nagara", "layout", "colony", "extension", "extn", "road", "main", "cross", "street", "lane",
        "sector", "phase", "block", "stage", "town", "city", "village", "puram", "pura", "pur", "halli",
        "east", "west", "north", "south", "new", "old", "the", "of", "and", "near", "bazar", "bazaar",
        "market", "marg", "chowk", "gali", "vihar", "enclave", "park", "garden", "gardens", "society",
        "apartment", "apartments", "residency", "towers", "tower", "complex", "india", "post", "office",
        "station", "camp", "cantonment", "industrial", "area", "estate", "ward", "mohalla", "basti",
        "district", "taluk", "tehsil", "mandal", "circle", "division", "region", "head", "sub", "branch",
        "upper", "lower", "middle", "central", "bus", "stop", "house", "no", "number", "flat", "floor",
        "building", "plot", "door", "opposite", "behind", "beside", "next", "to", "in", "at", "on",
        "kalan", "khurd", "bk", "bujurg", "budruk", "majra", "dhani", "khera", "kheda",
    }
)


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def compact_key(text: str) -> str:
    """Lowercase, ASCII, alphanumerics only, no spaces. 'Indira Nagar' == 'Indiranagar'."""
    return _NON_ALNUM.sub("", strip_accents(text).lower())


def spaced_key(text: str) -> str:
    """Lowercase ASCII words separated by single spaces."""
    return _WS.sub(" ", _NON_ALNUM.sub(" ", strip_accents(text).lower())).strip()


def collapse_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


_SMALL_WORDS = {"of", "and", "the", "in", "near", "ke", "ki", "ka"}


def smart_title(text: str) -> str:
    """Title-case ALL-CAPS dataset names while keeping acronyms and ordinals sane.

    'BENGALURU URBAN' -> 'Bengaluru Urban', 'HSR Layout' stays, '8th Phase' stays.
    """
    words = text.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if re.fullmatch(r"\d+(st|nd|rd|th)", lw) or i > 0 and lw in _SMALL_WORDS:
            out.append(lw)
        elif w.isupper() and len(w) <= 4 and not text.isupper():
            out.append(w)  # acronym inside mixed-case text (HSR, BTM, EPIP)
        elif w.isupper() or w.islower():
            out.append("-".join(p[:1].upper() + p[1:].lower() for p in w.split("-")))
        else:
            out.append(w)
    return " ".join(out)


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def is_generic_only(tokens: list[str]) -> bool:
    """True if every token is a generic address word or a number/ordinal."""
    for t in tokens:
        if t in GENERIC_PLACE_WORDS:
            continue
        if re.fullmatch(r"\d+(st|nd|rd|th)?", t):
            continue
        return False
    return True
