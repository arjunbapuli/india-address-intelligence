"""Number and ordinal normalisation for English, Hindi/Hinglish and Indic digits.

Handles:
  * English cardinals ("fourteen", "twenty one", "one hundred and five")
  * English ordinals ("third", "twenty first")
  * Hinglish/Hindi cardinals & ordinals ("teen", "chaudah", "teesra", "doosri")
  * Indic-script digits (०-९, ೦-೯, ௦-௯ ...)
  * Roman numerals in place names ("Koramangala VI Block" -> 6)
  * Spoken digit strings for PIN codes ("five six double zero three four" -> 560034)
"""

from __future__ import annotations

import unicodedata

EN_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
           "eighty": 80, "ninety": 90}
EN_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
    "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
}
# Hindi / Hinglish (romanised). Only unambiguous forms: "do" (2) is excluded
# because it is also an English word; it is handled in numeric slot context.
HI_CARDINALS = {
    "ek": 1, "teen": 3, "tin": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "chhah": 6,
    "che": 6, "saat": 7, "sat": 7, "aath": 8, "aat": 8, "nau": 9, "das": 10, "gyarah": 11, "gyara": 11,
    "barah": 12, "bara": 12, "terah": 13, "tera": 13, "chaudah": 14, "chauda": 14, "pandrah": 15,
    "pandra": 15, "solah": 16, "sola": 16, "satrah": 17, "satra": 17, "atharah": 18, "athara": 18,
    "unnis": 19, "bees": 20, "bis": 20, "pachis": 25, "pachchis": 25, "tees": 30, "chalis": 40,
    "pachas": 50, "pachaas": 50, "sau": 100,
}
HI_ORDINALS = {
    "pehla": 1, "pahla": 1, "pehli": 1, "pahli": 1, "pehle": 1, "doosra": 2, "dusra": 2, "doosri": 2,
    "dusri": 2, "doosre": 2, "teesra": 3, "tisra": 3, "teesri": 3, "tisri": 3, "teesre": 3, "chautha": 4,
    "chauthi": 4, "chauthe": 4, "paanchva": 5, "panchva": 5, "paanchwa": 5, "panchwa": 5, "paanchvi": 5,
    "chhatha": 6, "chhathi": 6, "chhata": 6, "saatva": 7, "satva": 7, "aathva": 8, "athva": 8,
    "nauva": 9, "dasva": 10, "dasvi": 10,
}
# Words that only become numbers next to a numeric slot word (sector/phase/cross ...)
# because they are common STT homophones of digits.
SLOT_HOMOPHONES = {
    "to": 2, "too": 2, "tu": 2, "do": 2, "for": 4, "fore": 4, "won": 1, "ate": 8, "tree": 3, "free": 3,
    "sex": 6, "tin": 3, "nein": 9,
}

ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
         "xi": 11, "xii": 12}

ALL_CARDINAL_WORDS = {**EN_UNITS, **EN_TENS, **HI_CARDINALS}
ALL_ORDINAL_WORDS = {**EN_ORDINALS, **HI_ORDINALS}


def indic_digits_to_ascii(text: str) -> str:
    """Convert any Unicode decimal digit (Devanagari, Kannada, Tamil, ...) to ASCII."""
    out = []
    for ch in text:
        if ch.isdigit() and not ("0" <= ch <= "9"):
            try:
                out.append(str(unicodedata.digit(ch)))
                continue
            except (TypeError, ValueError):
                pass
        out.append(ch)
    return "".join(out)


def parse_number_words(tokens: list[str], start: int, allow_hindi: bool = False) -> tuple[int | None, int, bool]:
    """Parse one number expressed in words starting at `start`.

    Returns (value, tokens_consumed, is_ordinal); value is None if there is no number word.
    Handles "fourteen", "twenty one", "twenty first", "one hundred and five", "third".
    Digit-by-digit sequences ("five six zero") are handled by `spoken_digit_run`.
    Hindi cardinals ("teen", "char", "bara") are only parsed when `allow_hindi` is set
    (i.e. next to a numeric slot word) because many are also place-name words
    ("Bara Bazar", "Char Minar").
    """
    n = len(tokens)
    i = start
    total = 0
    matched = False

    def unit(t: str) -> int | None:
        v = EN_UNITS.get(t)
        if v is None and allow_hindi:
            v = HI_CARDINALS.get(t)
        return v

    if i < n and (u := unit(tokens[i])) is not None and 0 < u < 10 and i + 1 < n and tokens[i + 1] == "hundred":
        total, i, matched = u * 100, i + 2, True
        if i + 1 < n and tokens[i] == "and" and (tokens[i + 1] in EN_UNITS or tokens[i + 1] in EN_TENS):
            i += 1
    if i < n and tokens[i] in EN_TENS:
        total += EN_TENS[tokens[i]]
        i += 1
        if i < n and tokens[i] in EN_UNITS and 0 < EN_UNITS[tokens[i]] < 10:
            return total + EN_UNITS[tokens[i]], i + 1 - start, False
        if i < n and tokens[i] in EN_ORDINALS and 0 < EN_ORDINALS[tokens[i]] < 10:
            return total + EN_ORDINALS[tokens[i]], i + 1 - start, True
        return total, i - start, False
    if i < n and (u := unit(tokens[i])) is not None and (u < 100 or not matched):
        return total + u, i + 1 - start, False
    ords = ALL_ORDINAL_WORDS if allow_hindi else {**EN_ORDINALS, **HI_ORDINALS}
    if i < n and tokens[i] in ords:
        return total + ords[tokens[i]], i + 1 - start, True
    if matched:
        return total, i - start, False
    return None, 0, False


_SPOKEN_DIGIT = {**{k: v for k, v in EN_UNITS.items() if v < 10}, "o": 0, "oh": 0}


def spoken_digit_run(tokens: list[str], start: int) -> tuple[str | None, int]:
    """Read a digit-by-digit spoken sequence, supporting 'double'/'triple'.

    Returns (digits, tokens_consumed). Requires at least 3 digits to avoid
    swallowing ordinary numbers.
    """
    digits: list[str] = []
    i = start
    while i < len(tokens):
        t = tokens[i]
        if t in ("double", "triple") and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            d = _SPOKEN_DIGIT.get(nxt)
            if d is None and nxt.isdigit() and len(nxt) == 1:
                d = int(nxt)
            if d is None:
                break
            digits.extend([str(d)] * (2 if t == "double" else 3))
            i += 2
            continue
        if t in _SPOKEN_DIGIT:
            digits.append(str(_SPOKEN_DIGIT[t]))
            i += 1
            continue
        if t.isdigit() and len(t) <= 3 and digits:
            digits.append(t)
            i += 1
            continue
        if t.isdigit() and len(t) <= 3 and not digits and i + 1 < len(tokens) and (
            tokens[i + 1] in _SPOKEN_DIGIT or tokens[i + 1] in ("double", "triple")
        ):
            digits.append(t)
            i += 1
            continue
        break
    joined = "".join(digits)
    if len(joined) >= 3 and i - start >= 3:
        return joined, i - start
    return None, 0


def roman_to_int(token: str) -> int | None:
    return ROMAN.get(token.lower())
