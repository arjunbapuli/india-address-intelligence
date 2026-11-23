"""Stage 1: input normalisation.

Turns a raw transcript into a lower-case, tokenised, Latin-script string while
recording every change as a `Correction`. Nothing here consults geography; it
only fixes *form* (case, punctuation, numbers, abbreviations, transliteration,
filler words, obvious STT homophones in numeric slots).

The original input is never modified; callers keep it alongside the result.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.intelligence.normalization.numbers import (
    HI_CARDINALS,
    SLOT_HOMOPHONES,
    indic_digits_to_ascii,
    parse_number_words,
    spoken_digit_run,
)
from app.intelligence.normalization.transliterate import to_latin
from app.intelligence.normalization.vocabulary import (
    ABBREVIATIONS,
    HOMOPHONE_SLOTS,
    PHRASES,
    POST_NUMERIC_SLOTS,
    PRE_ORDINAL_SLOTS,
    UNCERTAINTY_PATTERNS,
    UNCERTAINTY_STRIP,
)
from app.intelligence.text import collapse_ws, ordinal

# Correction types, from least to most "inferential". Only the last two can
# change *what* the address says rather than how it is written.
CORRECTION_TYPES = (
    "formatting",
    "filler_removal",
    "abbreviation",
    "number_normalization",
    "transliteration",
    "transcription_fix",  # obvious STT homophone in a numeric slot ("sector to" -> "sector 2")
    "spelling_correction",  # fuzzy/phonetic match to a known place name
    "entity_expansion",  # alias -> canonical ("HSR" -> "HSR Layout", "Bangalore" -> "Bengaluru")
    "inference",  # value derived from other evidence (e.g. PIN from locality)
)


@dataclass
class Correction:
    original: str
    corrected: str
    type: str
    reason: str
    stage: str = "normalization"


@dataclass
class NormalizedInput:
    original: str
    text: str
    tokens: list[str]
    corrections: list[Correction] = field(default_factory=list)
    languages: set[str] = field(default_factory=set)
    uncertainty_markers: list[str] = field(default_factory=list)


_PUNCT_KEEP = re.compile(r"[^\w\s,/#\-]")


def _record(corrs: list[Correction], original: str, corrected: str, ctype: str, reason: str) -> None:
    o, c = collapse_ws(original), collapse_ws(corrected)
    if o.lower() == c.lower() and ctype not in ("transliteration",):
        return
    corrs.append(Correction(original=o, corrected=c, type=ctype, reason=reason))


def _clean_punctuation(text: str) -> str:
    text = text.replace("&", " and ").replace("@", " at ")
    text = re.sub(r"(?<=[a-z])\.(?=[a-z])", "", text)  # h.no -> hno, s.o -> so
    text = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", text)  # 12 / 3 -> 12/3
    text = re.sub(r"(?<=\w)-(?=\d)|(?<=\d)-(?=\w)", "-", text)  # keep A-101, 12-B
    text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)  # word-word -> word word
    text = re.sub(r"[;|\n\r\t]+", " , ", text)
    text = text.replace(",", " , ")
    text = re.sub(r"#\s*(?=\w)", " no ", text)
    text = _PUNCT_KEEP.sub(" ", text)
    text = re.sub(r"(?<!\w)[-/](?!\w)", " ", text)
    return collapse_ws(text)


def _split_alnum(tokens: list[str]) -> list[str]:
    """'14main' -> '14 main', '3rdcross' -> '3rd cross', but keep 'a-101', '12b', '560102'."""
    out: list[str] = []
    for t in tokens:
        m = re.fullmatch(r"(\d+(?:st|nd|rd|th)?)(main|cross|stage|block|phase|floor|sector|street|road|lane)", t)
        if m:
            out.extend([m.group(1), m.group(2)])
            continue
        m = re.fullmatch(r"(sector|phase|block|stage|gali|lane|ward)(\d+)", t)
        if m:
            out.extend([m.group(1), m.group(2)])
            continue
        out.append(t)
    return out


def _normalise_numbers(tokens: list[str], corrs: list[Correction]) -> list[str]:
    out: list[str] = []
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i]
        prev = out[-1] if out else ""
        nxt = tokens[i + 1] if i + 1 < n else ""
        in_slot = prev in POST_NUMERIC_SLOTS or nxt in PRE_ORDINAL_SLOTS

        # Spoken digit strings (PIN codes, house numbers): "five six double zero three four"
        digits, used = spoken_digit_run(tokens, i)
        if digits:
            _record(corrs, " ".join(tokens[i:i + used]), digits, "number_normalization", "spoken digits")
            out.append(digits)
            i += used
            continue

        # STT homophones only in a strong numeric slot: "sector to" -> "sector 2"
        if prev in HOMOPHONE_SLOTS and t in SLOT_HOMOPHONES and not (t == "to" and nxt.isdigit()):
            val = SLOT_HOMOPHONES[t]
            _record(corrs, f"{prev} {t}", f"{prev} {val}", "transcription_fix",
                    f"'{t}' after '{prev}' is a common speech-to-text error for {val}")
            out.append(str(val))
            i += 1
            continue

        value, used, is_ord = parse_number_words(tokens, i, allow_hindi=in_slot)
        if value is not None:
            words = " ".join(tokens[i:i + used])
            follower = tokens[i + used] if i + used < n else ""
            if is_ord or follower in PRE_ORDINAL_SLOTS or follower in ("manzil", "manjil", "mazil"):
                rendered = ordinal(value)
            else:
                rendered = str(value)
            _record(corrs, words, rendered, "number_normalization", "number words to digits")
            out.append(rendered)
            i += used
            continue

        # "14 main" -> "14th main"; "2 stage" -> "2nd stage"
        if re.fullmatch(r"\d{1,3}", t) and nxt in PRE_ORDINAL_SLOTS and prev not in POST_NUMERIC_SLOTS:
            rendered = ordinal(int(t))
            _record(corrs, f"{t} {nxt}", f"{rendered} {nxt}", "formatting", "ordinal form for main/cross/stage")
            out.append(rendered)
            i += 1
            continue
        # Hindi numbers right after a slot word that the generic parser skipped
        if prev in POST_NUMERIC_SLOTS and t in HI_CARDINALS:
            _record(corrs, t, str(HI_CARDINALS[t]), "number_normalization", "Hindi number word")
            out.append(str(HI_CARDINALS[t]))
            i += 1
            continue
        # "1st manzil" -> "1st floor"
        if t in ("manzil", "manjil", "mazil") and re.fullmatch(r"\d+(st|nd|rd|th)", prev):
            _record(corrs, t, "floor", "abbreviation", "Hindi 'manzil' = floor")
            out.append("floor")
            i += 1
            continue
        out.append(t)
        i += 1
    return out


def _expand_abbreviations(tokens: list[str], corrs: list[Correction]) -> list[str]:
    out = []
    for idx, t in enumerate(tokens):
        nxt = tokens[idx + 1] if idx + 1 < len(tokens) else ""
        prev = tokens[idx - 1] if idx > 0 else ""
        if t in ABBREVIATIONS and ABBREVIATIONS[t] != t:
            exp = ABBREVIATIONS[t]
            _record(corrs, t, exp, "abbreviation", "common address abbreviation")
            out.append(exp)
        elif t == "st" and (nxt in ("", ",") or re.fullmatch(r"\d+(st|nd|rd|th)?", prev)):
            _record(corrs, t, "street", "abbreviation", "common address abbreviation")
            out.append("street")
        elif t == "bk" and re.fullmatch(r"\d+(st|nd|rd|th)?", prev):
            _record(corrs, t, "block", "abbreviation", "common address abbreviation")
            out.append("block")
        elif t == "x" and re.fullmatch(r"\d+(st|nd|rd|th)", prev):
            _record(corrs, t, "cross", "abbreviation", "'x' after an ordinal means cross road")
            out.append("cross")
        else:
            out.append(t)
    return out


def normalize(text: str) -> NormalizedInput:
    original = text
    corrs: list[Correction] = []
    s = unicodedata.normalize("NFKC", text)
    s = indic_digits_to_ascii(s)

    latin, langs = to_latin(s)
    if langs:
        _record(corrs, s, latin, "transliteration", f"transliterated {', '.join(sorted(langs))} script to Latin")
    s = latin.lower()
    s = _clean_punctuation(s)

    uncertainty: list[str] = []
    seen_spans: list[tuple[int, int]] = []
    for p in UNCERTAINTY_PATTERNS:
        for m in re.finditer(p, s):
            if not any(a <= m.start() < b for a, b in seen_spans):
                seen_spans.append(m.span())
                uncertainty.append(m.group(0))
    for p in UNCERTAINTY_STRIP:
        s = re.sub(p, " ", s)

    for pattern, repl, ctype, reason in PHRASES:
        def _sub(m: re.Match[str], repl: str = repl, ctype: str = ctype, reason: str = reason) -> str:
            shown = repl.strip()
            if shown.startswith("_"):
                shown = shown.strip("_").replace("post", "")
            if shown != ",":
                _record(corrs, m.group(0), shown, ctype, reason)
            return repl

        s = re.sub(pattern, _sub, s)
    s = collapse_ws(s)

    tokens = s.split()
    tokens = _split_alnum(tokens)
    tokens = _expand_abbreviations(tokens, corrs)
    tokens = _normalise_numbers(tokens, corrs)

    # Tidy separators: no leading/trailing/double commas
    cleaned: list[str] = []
    for t in tokens:
        if t == "," and (not cleaned or cleaned[-1] == ","):
            continue
        cleaned.append(t)
    while cleaned and cleaned[-1] == ",":
        cleaned.pop()

    return NormalizedInput(
        original=original,
        text=" ".join(cleaned),
        tokens=cleaned,
        corrections=corrs,
        languages=langs or {"en"},
        uncertainty_markers=uncertainty,
    )
