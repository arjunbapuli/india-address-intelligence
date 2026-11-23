"""Phonetic keys tuned for romanised Indian place names.

Generic algorithms (Soundex, Metaphone) are designed for English surnames and
miss the variation patterns that matter here:

  * aspirates written with/without 'h':  Dharwad / Darwad, Bhopal / Bopal
  * vowel length and dropped vowels:     Koramangala / Kormangla, Indiranagar / Indranagar
  * v / w / b:                            Vasant / Wasant, Bhubaneswar / Bhubaneshbar
  * s / sh / z, j / z, ph / f, q / k:     Shivaji / Sivaji, Zakir / Jakir
  * doubled consonants:                   Ballari / Bellary, Pallavaram / Palavaram
  * common suffix spellings:              -halli / -hally, -pur / -pura / -puram
  * Anglicised spellings:                 Bengaluru / Bangalore -> same skeleton

`indic_phonetic_key` returns a consonant skeleton (vowels removed except a
leading vowel) that is identical for most of these variants. It is used only
to *generate* candidates; final scoring uses string similarity.
"""

from __future__ import annotations

import re
from functools import lru_cache

import jellyfish

from app.intelligence.text import compact_key

_DIGRAPHS = [
    ("chh", "%"), ("ksh", "ks"), ("x", "ks"), ("kh", "k"), ("gh", "g"), ("ch", "%"), ("jh", "j"), ("th", "t"),
    ("dh", "d"), ("ph", "f"), ("bh", "b"), ("sh", "s"), ("zh", "l"), ("ck", "k"), ("qu", "k"), ("q", "k"),
    ("wh", "v"), ("w", "v"), ("z", "j"), ("ee", "i"), ("oo", "u"), ("ou", "u"), ("au", "o"),
]
_SUFFIXES = [
    (r"hally$", "halli"), (r"halli$", "hli"), (r"palli$", "pli"), (r"palle$", "pli"), (r"puram$", "prm"),
    (r"pura$", "pr"), (r"pur$", "pr"), (r"nagara$", "ngr"), (r"nagar$", "ngr"), (r"abad$", "bd"),
    (r"garh$", "gr"), (r"ganj$", "gnj"), (r"wadi$", "vdi"), (r"wada$", "vd"), (r"kere$", "kr"), (r"uru$", "r"),
    (r"ore$", "r"),
]


@lru_cache(maxsize=200_000)
def indic_phonetic_key(text: str) -> str:
    s = compact_key(text)
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    s = "".join(ch for ch in s if ch.isalpha())
    if not s:
        return digits
    for pat, rep in _SUFFIXES:
        s = re.sub(pat, rep, s)
    for a, b in _DIGRAPHS:
        s = s.replace(a, b)
    s = re.sub(r"c(?=[eiy])", "s", s).replace("c", "k").replace("%", "c")
    s = s.replace("v", "b")  # v/w/b merge (Bengali/Odia b~v, Hindi v~w)
    s = s.replace("y", "i")
    first, rest = s[0], s[1:]
    rest = re.sub(r"[aeiouh]", "", rest)
    s = first + rest
    s = re.sub(r"(.)\1+", r"\1", s)  # collapse doubles
    return s + digits


def double_metaphone_like(text: str) -> str:
    """Secondary key from jellyfish metaphone (handles English-origin names)."""
    try:
        return jellyfish.metaphone(compact_key(text))
    except Exception:  # pragma: no cover - defensive
        return ""


def phonetic_similarity(a: str, b: str) -> float:
    """0..1 similarity of phonetic skeletons (Jaro-Winkler on keys)."""
    ka, kb = indic_phonetic_key(a), indic_phonetic_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    return jellyfish.jaro_winkler_similarity(ka, kb)
