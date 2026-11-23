"""Stages 3 & 5: entity identification and candidate generation.

For every free token span left by extraction we generate n-grams and look
them up in the gazetteer, in order of decreasing reliability:

  exact name  >  alias (old name, acronym, transliteration)  >  phonetic  >  fuzzy

Longer exact matches win over shorter ones; fuzzy matching is only tried on
windows that nothing better explained, and is scoped to the districts implied
by any city / district / PIN the user mentioned (much higher precision than a
global fuzzy search over 150k names).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.geography.gazetteer import Gazetteer
from app.intelligence.normalization.vocabulary import COMMON_WORDS
from app.intelligence.phonetics.indic import phonetic_similarity
from app.intelligence.text import compact_key, is_generic_only

ADMIN = ("state", "district", "city")
LOCAL = ("locality", "osm_place", "street", "landmark")

MATCH_TYPE_SCORE = {"exact": 1.0, "alias": 0.95, "phonetic": 0.8, "fuzzy": 0.7, "prefix": 0.6}


@dataclass
class Candidate:
    place_id: int
    match_type: str  # exact | alias | phonetic | fuzzy
    similarity: float  # 0..1 string similarity between input text and matched name
    alias_type: str | None = None
    matched_key: str | None = None


@dataclass
class Mention:
    start: int
    end: int
    text: str
    candidates: list[Candidate] = field(default_factory=list)

    def best_type(self, g: Gazetteer) -> set[str]:
        return {g.places[c.place_id].type for c in self.candidates}


def _hits_to_candidates(g: Gazetteer, text: str, hits) -> list[Candidate]:
    out = []
    for h in hits:
        mt = "exact" if h.kind == "name" else "alias"
        out.append(Candidate(h.place_id, mt, 1.0 if mt == "exact" else h.confidence,
                             alias_type=None if mt == "exact" else h.kind.split(":", 1)[1]))
    return out


def lookup_window(g: Gazetteer, words: list[str], allow_fuzzy: bool, scope_keys: dict[str, list[int]] | None,
                  fuzzy_cutoff_global: float = 88.0, fuzzy_cutoff_scoped: float = 80.0, skip_exact: bool = False
                  ) -> tuple[str, list[Candidate]]:
    """Look up one n-gram. Returns (match_type, candidates)."""
    text = " ".join(words)
    key = compact_key(text)
    if not key or is_generic_only(words):
        return "none", []
    hits = [] if skip_exact else g.lookup_exact(text)
    if hits:
        return "exact", _hits_to_candidates(g, text, hits)
    if len(key) < 4 or all(w in COMMON_WORDS for w in words):
        return "none", []
    # Phonetic. Unscoped (India-wide) phonetic matches need a longer key and higher string
    # similarity: short skeletons collide with thousands of village names.
    scoped = scope_keys is not None
    scope_ids = {pid for ids in scope_keys.values() for pid in ids} if scope_keys else set()
    min_len, min_sim = (4, 0.6) if scoped else (5, 0.78)
    ph = g.lookup_phonetic_keys(text) if len(key) >= min_len else []
    if ph:
        best: dict[int, Candidate] = {}
        for pid, mkey in ph:
            p = g.places[pid]
            if scoped and pid not in scope_ids and p.type not in ("city", "district", "state"):
                continue
            sim = fuzz.ratio(key, mkey) / 100.0
            if sim < min_sim:
                continue
            psim = phonetic_similarity(text, mkey)
            c = Candidate(pid, "phonetic", round(0.5 * sim + 0.5 * psim, 3), matched_key=mkey)
            if pid not in best or c.similarity > best[pid].similarity:
                best[pid] = c
        if best:
            return "phonetic", list(best.values())
    if not allow_fuzzy:
        return "none", []
    cands = []
    if scope_keys:
        for k, score in g.fuzzy_keys(text, limit=10, score_cutoff=fuzzy_cutoff_scoped, choices=list(scope_keys)):
            for pid in scope_keys[k]:
                cands.append(Candidate(pid, "fuzzy", round(score / 100.0, 3), matched_key=k))
    if not cands and len(key) >= 5:
        for k, score in g.fuzzy_keys(text, limit=8, score_cutoff=fuzzy_cutoff_global):
            for h in g.exact.get(k, ()):
                cands.append(Candidate(h.place_id, "fuzzy", round(score / 100.0, 3), matched_key=k,
                                       alias_type=None if h.kind == "name" else h.kind.split(":", 1)[1]))
    return ("fuzzy", cands) if cands else ("none", [])


def find_mentions(g: Gazetteer, tokens: list[str], spans: list[tuple[int, int]], max_n: int = 5,
                  scope_keys: dict[str, list[int]] | None = None, allow_fuzzy: bool = True) -> tuple[
                      list[Mention], list[tuple[int, int]]]:
    """Segment each span into gazetteer mentions. Returns (mentions, unmatched_spans)."""
    mentions: list[Mention] = []
    unmatched: list[tuple[int, int]] = []
    for s, e in spans:
        used = [False] * (e - s)
        found: list[Mention] = []
        # Pass 1: exact/alias/phonetic, longest first
        for size in range(min(max_n, e - s), 0, -1):
            for i in range(s, e - size + 1):
                j = i + size
                if any(used[i - s:j - s]):
                    continue
                words = tokens[i:j]
                mt, cands = lookup_window(g, words, allow_fuzzy=False, scope_keys=None)
                if cands:
                    found.append(Mention(i, j, " ".join(words), cands))
                    for k in range(i, j):
                        used[k - s] = True
        # Pass 2: fuzzy on remaining runs (whole run first, then shorter windows)
        if allow_fuzzy:
            for size in range(min(max_n, e - s), 0, -1):
                for i in range(s, e - size + 1):
                    j = i + size
                    if any(used[i - s:j - s]):
                        continue
                    words = tokens[i:j]
                    mt, cands = lookup_window(g, words, allow_fuzzy=True, scope_keys=scope_keys)
                    if cands:
                        found.append(Mention(i, j, " ".join(words), cands))
                        for k in range(i, j):
                            used[k - s] = True
        mentions.extend(found)
        # Unmatched runs inside the span
        k = s
        while k < e:
            if not used[k - s]:
                m = k
                while m < e and not used[m - s]:
                    m += 1
                unmatched.append((k, m))
                k = m
            else:
                k += 1
    mentions.sort(key=lambda m: m.start)
    return mentions, unmatched


def best_similarity(c: Candidate) -> float:
    return c.similarity * MATCH_TYPE_SCORE.get(c.match_type, 0.5)
