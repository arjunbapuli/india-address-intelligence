"""Indic-script -> Latin transliteration tuned for matching (not for display).

Uses `indic-transliteration` (sanscript) to romanise Devanagari, Bengali,
Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada and Malayalam into IAST, then
simplifies IAST into the plain ASCII spelling an Indian user would type
("इंदिरानगर" -> "indiranagar", "तीसरा" -> "tisra", "घर नंबर" -> "ghar nambar").

Output is intended for the phonetic/fuzzy matchers, which are tolerant of
remaining vowel differences.
"""

from __future__ import annotations

import re
import unicodedata

from indic_transliteration import sanscript
from indic_transliteration.sanscript import transliterate as _sanscript_transliterate

# Unicode block ranges -> sanscript scheme, and whether the language drops the
# inherent final schwa ("nagara" -> "nagar").
_SCRIPTS: list[tuple[int, int, str, bool, str]] = [
    (0x0900, 0x097F, sanscript.DEVANAGARI, True, "hi"),
    (0x0980, 0x09FF, sanscript.BENGALI, True, "bn"),
    (0x0A00, 0x0A7F, sanscript.GURMUKHI, True, "pa"),
    (0x0A80, 0x0AFF, sanscript.GUJARATI, True, "gu"),
    (0x0B00, 0x0B7F, sanscript.ORIYA, True, "or"),
    (0x0B80, 0x0BFF, sanscript.TAMIL, False, "ta"),
    (0x0C00, 0x0C7F, sanscript.TELUGU, False, "te"),
    (0x0C80, 0x0CFF, sanscript.KANNADA, False, "kn"),
    (0x0D00, 0x0D7F, sanscript.MALAYALAM, False, "ml"),
]

# Pre-fixes for characters sanscript does not map well.
_PRE = {
    "ॉ": "ो",  # Devanagari candra O (ॉ as in क्रॉस) -> O
    "ॅ": "े",  # candra E -> E
    "ऑ": "ओ",  # independent candra O
    "ୱ": "ବ",  # Odia wa -> ba-ish
    "‌": "",  # ZWNJ
    "‍": "",  # ZWJ
}

_IAST_VOWEL = "aāiīuūṛṝeèoòéó"
_VOWEL_RE = re.compile(f"(ai|au|[{_IAST_VOWEL}])")


def script_of(ch: str) -> tuple[str, bool, str] | None:
    cp = ord(ch)
    for lo, hi, scheme, schwa, lang in _SCRIPTS:
        if lo <= cp <= hi:
            return scheme, schwa, lang
    return None


def detect_scripts(text: str) -> set[str]:
    langs = set()
    for ch in text:
        s = script_of(ch)
        if s:
            langs.add(s[2])
    return langs


def _is_vowel_at(word: str, i: int) -> bool:
    return word[i] in _IAST_VOWEL


def _schwa_delete(word: str) -> str:
    """Approximate Hindi schwa deletion on an IAST word."""
    if len(word) < 3:
        return word
    # Final schwa: consonant + 'a' at end (but not a diphthong / not after another vowel)
    if word.endswith("a") and not _is_vowel_at(word, len(word) - 2) and not word.endswith(("ya",)) and len(word) > 2:
        word = word[:-1]
    # Medial schwa (V C a C V -> V C C V) only for short words; compounds break the rule.
    if len(_VOWEL_RE.findall(word)) <= 3:
        chars = list(word)
        for i in range(len(chars) - 2, 1, -1):
            if chars[i] != "a":
                continue
            if i + 1 < len(chars) and chars[i + 1] in ("i", "u"):
                continue  # diphthong
            prev_c = chars[i - 1]
            next_c = chars[i + 1] if i + 1 < len(chars) else ""
            after = chars[i + 2] if i + 2 < len(chars) else ""
            before_prev = chars[i - 2] if i >= 2 else ""
            if (
                prev_c not in _IAST_VOWEL
                and prev_c != "h"
                and next_c
                and next_c not in _IAST_VOWEL
                and next_c != "h"
                and after in _IAST_VOWEL
                and before_prev in _IAST_VOWEL
            ):
                del chars[i]
                break
        word = "".join(chars)
    return word


def _iast_to_ascii(s: str) -> str:
    s = s.replace("ch", "\x00")  # IAST 'ch' = aspirated (chh)
    s = s.replace("c", "ch").replace("\x00", "chh")
    # Anusvara: m before labials, n otherwise
    s = re.sub(r"ṃ(?=[pbm])", "m", s)
    s = s.replace("ṃ", "n").replace("ṁ", "n")
    repl = {
        "ā": "a", "ī": "i", "ū": "u", "ṛ": "ri", "ṝ": "ri", "ṅ": "n", "ñ": "n", "ṇ": "n", "ṭ": "t",
        "ḍ": "d", "ś": "sh", "ṣ": "sh", "ḥ": "h", "ḻ": "l", "ḷ": "l", "ṟ": "r", "ṉ": "n", "è": "e",
        "ò": "o", "é": "e", "ó": "o", "ē": "e", "ō": "o", "ṯ": "t", "ḵ": "k", "ġ": "g", "ż": "z",
    }
    s = "".join(repl.get(ch, ch) for ch in s)
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if ord(ch) < 128)


def transliterate_run(run: str, scheme: str, drop_schwa: bool) -> str:
    for a, b in _PRE.items():
        run = run.replace(a, b)
    iast = _sanscript_transliterate(run, scheme, sanscript.IAST).lower()
    words = re.split(r"(\s+)", iast)
    out = []
    for w in words:
        if w.strip() and drop_schwa:
            w = _schwa_delete(w)
        out.append(w)
    ascii_text = _iast_to_ascii("".join(out))
    # English loanwords written in Indic script ("सेक्टर", "क्रॉस", "लेआउट") back to English.
    return re.sub(r"[a-z]+", lambda m: LOANWORDS.get(m.group(0), m.group(0)), ascii_text)


# Romanised forms of English address words as they come out of Indic scripts.
LOANWORDS = {
    "sektar": "sector", "sekTar": "sector", "sekter": "sector", "kros": "cross", "kras": "cross",
    "krosa": "cross", "men": "main", "mena": "main", "leaut": "layout", "leaaut": "layout",
    "leout": "layout", "rod": "road", "roda": "road", "strit": "street", "nambar": "number",
    "nanbar": "number", "phlait": "flat", "phlet": "flat", "phlaiT": "flat", "flait": "flat",
    "blak": "block", "blok": "block", "phej": "phase", "fej": "phase", "stej": "stage", "plot": "plot",
    "flor": "floor", "phlor": "floor", "apartament": "apartment", "aparTament": "apartment",
    "kolani": "colony", "kaloni": "colony", "nagara": "nagar", "manjil": "manzil", "chauk": "chowk",
    "taavar": "tower", "tavar": "tower", "sosaiti": "society", "sosayati": "society", "hausa": "house",
    "haus": "house", "nyu": "new", "olD": "old", "bilDing": "building", "bilding": "building",
    "pahali": "pehli", "pahala": "pehla", "dusari": "doosri", "tisari": "teesri",
    "kalij": "college", "kolej": "college", "haspatal": "hospital", "mandira": "mandir",
    "masjida": "masjid", "skul": "school", "isakul": "school", "steshan": "station", "steshana": "station",
}


def to_latin(text: str) -> tuple[str, set[str]]:
    """Transliterate every Indic-script run in `text`. Returns (latin_text, languages_detected)."""
    langs: set[str] = set()
    out: list[str] = []
    buf: list[str] = []
    cur: tuple[str, bool, str] | None = None

    def flush() -> None:
        nonlocal buf, cur
        if buf and cur:
            out.append(transliterate_run("".join(buf), cur[0], cur[1]))
        buf = []

    for ch in text:
        sc = script_of(ch)
        if sc is not None:
            if cur is not None and sc[0] != cur[0]:
                flush()
            cur = sc
            langs.add(sc[2])
            buf.append(ch)
        elif cur is not None and ch in "‌‍":
            buf.append(ch)
        else:
            if buf:
                flush()
                cur = None
            out.append(ch)
    flush()
    return "".join(out), langs
