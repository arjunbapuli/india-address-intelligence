"""Indian address vocabulary: abbreviations, Hinglish phrases, filler words.

Everything here is *language* knowledge, not geography. Place names live in
the gazetteer (database), never in this file.
"""

from __future__ import annotations

# Token-level abbreviation expansions. Applied to whole tokens only.
ABBREVIATIONS: dict[str, str] = {
    "rd": "road", "nr": "near", "opp": "opposite", "opps": "opposite", "oppo": "opposite",
    "opposit": "opposite", "oposite": "opposite", "bhd": "behind", "bhnd": "behind", "apt": "apartment",
    "apts": "apartments", "appt": "apartment", "aptmt": "apartment", "aprtmnt": "apartment", "soc": "society",
    "socy": "society", "scty": "society", "blk": "block", "sec": "sector", "sect": "sector", "secter": "sector",
    "sectr": "sector", "ph": "phase", "phs": "phase", "stg": "stage", "extn": "extension", "extension": "extension",
    "ext": "extension", "ngr": "nagar", "lyt": "layout", "layot": "layout", "clny": "colony",
    "colny": "colony", "cly": "colony", "mn": "main", "crs": "cross", "crss": "cross", "flr": "floor", "fl": "floor",
    "bldg": "building", "bldng": "building", "bld": "building", "hsg": "housing", "indl": "industrial",
    "mkt": "market", "stn": "station", "hosp": "hospital", "clg": "college", "univ": "university",
    "govt": "government", "dist": "district", "distt": "district", "tq": "taluk", "tal": "taluk", "vill": "village",
    "vil": "village", "vlg": "village", "sq": "square", "ave": "avenue", "jn": "junction", "jnc": "junction",
    "jct": "junction", "twr": "tower", "cmplx": "complex", "encl": "enclave", "gdn": "garden", "gdns": "gardens",
    "pkt": "pocket", "nagr": "nagar", "naggar": "nagar", "lane": "lane", "ln": "lane",
    "galli": "gali", "sadak": "road", "sarak": "road", "chauraha": "chowk", "chauk": "chowk", "nambar": "number", "numbar": "number",
    "nmbr": "number", "num": "number", "nbr": "number", "rasta": "road", "raasta": "road", "flt": "flat",
    "hse": "house", "frst": "first", "secnd": "second",
}

# Multi-word phrase rewrites (regex, applied to lowercased Latin text, longest first).
# Each: (pattern, replacement, correction_type, reason)
# Special marker tokens:
#   near / opposite / behind / beside  : landmark FOLLOWS ("near NIFT")
#   _nearpost_ / _opppost_ / ...       : landmark PRECEDES (Hinglish "NIFT ke paas")
PHRASES: list[tuple[str, str, str, str]] = [
    # Fillers / conversational wrappers (removed)
    (r"\b(mera|meri|mere|my|the|our|hamara)\s+(address|addr|pata|ghar|location|delivery address)\s+(hai|he|h|is|will be|should be)\b", " ", "filler_removal", "conversational filler"),
    (r"\b(address|pata|location)\s+(hai|is)\b", " ", "filler_removal", "conversational filler"),
    (r"\b(please|pls|plz|kindly)\s+(deliver|send|ship)\s+(it\s+)?(to|at)\b", " ", "filler_removal", "conversational filler"),
    (r"\b(deliver|send|ship)\s+(it\s+)?(to|at)\b", " ", "filler_removal", "conversational filler"),
    (r"\bi\s+(stay|live|reside|am staying|am living)\s+(in|at|near)?\b", " ", "filler_removal", "conversational filler"),
    (r"\b(main|mai|mein|hum)\s+(rehta|rehti|rahta|rahti|rehte|rahte)\s+(hoon|hun|hu|hain|hai)\b", " ", "filler_removal", "conversational filler"),
    (r"\b(rehta|rehti|rahta|rahti|rehte|rahte)\s+(hoon|hun|hu|hain|hai)\b", " ", "filler_removal", "conversational filler"),
    (r"\b(it is|it's|its|that is|this is|which is)\b", " ", "filler_removal", "conversational filler"),
    (r"\b(um+|uh+|umm+|hmm+|er+|aa+|haan|han|ok|okay|ji|sir|madam|mam|ma'am|bhaiya|bhai|actually|basically)\b", " ", "filler_removal", "speech disfluency"),
    # Hinglish postpositions -> markers (landmark precedes)
    (r"\b(ke|ki|ka)\s+(bilkul\s+)?(paas|pass|pas|nazdeek|nazdik|najdeek|najdik|kareeb|karib|nearby)(\s+(mein|me|mai|main))?\b", " _nearpost_ ", "formatting", "Hinglish 'ke paas' = near"),
    (r"\b(ke|ki|ka)\s+(saamne|samne|samane|saamane|samney|opposite)\b", " _opppost_ ", "formatting", "Hinglish 'ke saamne' = opposite"),
    (r"\b(ke|ki|ka)\s+(peeche|piche|pichhe|peechhe|pichey|behind)\b", " _behindpost_ ", "formatting", "Hinglish 'ke peeche' = behind"),
    (r"\b(ke|ki|ka)\s+(bagal|baju|side)(\s+(mein|me|mai|main))?\b", " _besidepost_ ", "formatting", "Hinglish 'ke bagal mein' = beside"),
    (r"\b(paas|pass)\s+(mein|me|mai)\b", " _nearpost_ ", "formatting", "Hinglish 'paas mein' = near"),
    # English multi-word prepositions
    (r"\b(next\s+to|close\s+to|adjacent\s+to|right\s+beside|just\s+beside)\b", " beside ", "formatting", "landmark preposition"),
    (r"\b(in\s+front\s+of|across\s+from|opposite\s+to|opposite\s+of)\b", " opposite ", "formatting", "landmark preposition"),
    (r"\b(near\s+to|near\s+by|nearby|close\s+by)\b", " near ", "formatting", "landmark preposition"),
    (r"\b(at\s+the\s+back\s+of|behind\s+of)\b", " behind ", "formatting", "landmark preposition"),
    # Unit / house markers
    (r"\b(ghar|makan|makaan|mkn|house|h|door|d|home)\s*(no|number|nambar|numbar|nmbr|num|nbr|#)\b", " house no ", "formatting", "house number marker"),
    (r"\bhno\b", " house no ", "abbreviation", "abbreviation 'H.No'"),
    (r"\b(flat|flt|apartment|apt|unit)\s*(no|number|nambar|num|#)\b", " flat no ", "formatting", "flat number marker"),
    (r"\b(plot)\s*(no|number|nambar|num|#)\b", " plot no ", "formatting", "plot number marker"),
    (r"\b(gali|galli|gully|lane)\s*(no|number|nambar|numbar|num|#)\b", " gali no ", "formatting", "lane number marker"),
    (r"\b(shop)\s*(no|number|nambar|num|#)\b", " shop no ", "formatting", "shop number marker"),
    (r"\bc\s*/\s*o\b", " care of ", "formatting", "care-of marker"),
    (r"\b(wali|wala|wale|waali|waala)\b", " ", "filler_removal", "Hinglish connector"),
    # 'mein'/'me' as "in": acts as a separator between components
    (r"\b(mein|mei|me)\b", " , ", "formatting", "Hinglish 'mein' = in"),
    (r"\b(aur|and also)\b", " , ", "formatting", "conjunction"),
    (r"\b(hai|hain|he)\s*$", " ", "filler_removal", "conversational filler"),
]

# Words signalling the speaker is unsure; they reduce confidence.
UNCERTAINTY_PATTERNS: list[str] = [
    r"\bsomewhere\s+(in|near|around)\b",
    r"\bsomewhere\b",
    r"\b(i\s+think|maybe|probably|perhaps|not\s+sure|i\s+guess)\b",
    r"\b(shayad|lagbhag|pata\s+nahi|pakka\s+nahi|kahin)\b",
    r"\b(around|approximately|approx)\b",
]
UNCERTAINTY_STRIP = [r"\bsomewhere\s+(in|near|around)\b", r"\bsomewhere\b", r"\b(i\s+think|maybe|probably|perhaps|not\s+sure|i\s+guess)\b", r"\b(shayad|lagbhag|pata\s+nahi|pakka\s+nahi|kahin)\b"]

# Words after which a number is a *cardinal* label: "sector 2", "gali no 4", "phase 1"
POST_NUMERIC_SLOTS = frozenset(
    {"sector", "phase", "block", "stage", "gali", "lane", "ward", "no", "number", "plot", "flat", "house",
     "door", "tower", "wing", "unit", "pocket", "street", "khasra", "survey", "site", "shop", "room",
     "avenue", "zone", "floor"}
)
# Words before which a number becomes an *ordinal*: "14 main" -> "14th main", "3 cross" -> "3rd cross"
PRE_ORDINAL_SLOTS = frozenset(
    {"main", "cross", "stage", "block", "phase", "floor", "street", "avenue", "lane", "sector", "road", "gali",
     "mile"}
)
# Slots for which homophone digits ("to" -> 2) are accepted (strong numeric expectation)
HOMOPHONE_SLOTS = frozenset({"sector", "phase", "block", "stage", "gali", "ward", "no", "number", "pocket",
                             "floor", "tower", "zone"})

# Generic landmark nouns: a landmark made only of these can't be geographically verified.
GENERIC_LANDMARK_WORDS = frozenset(
    {"temple", "mandir", "mosque", "masjid", "church", "gurudwara", "school", "college", "hospital", "clinic",
     "park", "bus", "stop", "stand", "station", "railway", "metro", "petrol", "pump", "bank", "atm", "market",
     "mall", "shop", "store", "hotel", "office", "police", "post", "water", "tank", "tower", "circle", "signal",
     "junction", "bridge", "flyover", "ground", "playground", "lake", "pond", "well", "tree", "old", "new",
     "big", "small", "the", "a", "main", "gate", "chowk", "square", "corner", "medical", "pharmacy", "kirana",
     "dhaba", "restaurant", "theatre", "cinema", "gym", "library", "panchayat", "anganwadi"}
)

LANDMARK_PREPOSITIONS = frozenset({"near", "opposite", "behind", "beside", "besides", "adjacent", "facing"})
LANDMARK_POSTPOSITIONS = {"_nearpost_": "near", "_opppost_": "opposite", "_behindpost_": "behind",
                          "_besidepost_": "beside"}

# Common English / Hinglish words that are not place names. They may still match a
# place *exactly* (there are villages called "Police" or "Wait"), but they are never
# matched approximately (phonetic / fuzzy), which would turn chatter into places.
COMMON_WORDS = frozenset(
    ["a", "about", "above", "after", "again", "against", "all", "also", "am", "an", "and", "any", "are", "around", "as", "ask", "at", "away", "back", "bad", "be", "because", "been", "before", "behind", "being", "below", "beside", "best", "better", "between", "big", "both", "but", "by", "call", "came", "can", "come", "could", "day", "did", "do", "does", "done", "down", "during", "each", "early", "either", "else", "end", "even", "ever", "every", "few", "find", "first", "for", "found", "from", "front", "full", "get", "give", "go", "going", "gone", "good", "got", "great", "had", "has", "have", "having", "he", "her", "here", "him", "his", "home", "how", "i", "if", "in", "inside", "into", "is", "it", "its", "just", "keep", "kind", "know", "last", "late", "left", "less", "let", "like", "line", "little", "long", "look", "lot", "made", "make", "many", "may", "me", "might", "more", "most", "much", "must", "my", "near", "need", "never", "new", "next", "nice", "no", "not", "nothing", "now", "number", "of", "off", "often", "old", "on", "once", "one", "only", "open", "or", "other", "our", "out", "outside", "over", "own", "part", "people", "place", "please", "point", "put", "quite", "rather", "really", "right", "road", "said", "same", "say", "see", "seen", "she", "should", "show", "side", "since", "small", "so", "some", "something", "somewhere", "soon", "still", "such", "sure", "take", "tell", "than", "thank", "thanks", "that", "the", "their", "them", "then", "there", "these", "they", "thing", "think", "this", "those", "though", "through", "till", "time", "to", "today", "told", "too", "top", "towards", "try", "turn", "two", "under", "unclear", "until", "up", "upon", "us", "use", "very", "wait", "want", "was", "way", "we", "well", "went", "were", "what", "when", "where", "which", "while", "who", "whole", "why", "will", "with", "within", "without", "would", "yes", "yet", "you", "your", "address", "delivery", "deliver", "order", "parcel", "courier", "package", "phone", "call", "hello", "hi", "sir", "madam", "ji", "okay", "ok", "please", "building", "house", "flat", "floor", "home", "office", "shop", "store", "mall", "market", "school", "college", "hospital", "temple", "church", "mosque", "station", "stop", "bus", "metro", "railway", "park", "garden", "lake", "bridge", "tower", "gate", "street", "lane", "road", "cross", "main", "sector", "block", "phase", "stage", "colony", "layout", "nagar", "area", "city", "town", "village", "district", "state", "country", "india", "pin", "code", "pincode", "hai", "hain", "tha", "thi", "ka", "ki", "ke", "ko", "se", "mein", "me", "par", "aur", "ya", "bhi", "nahi", "haan", "ji", "wala", "wali", "wale", "ghar", "makan", "pass", "paas", "samne", "peeche", "bagal", "yahan", "wahan", "kahan", "kya", "kaun", "kaise", "mera", "meri", "mere", "tera", "teri", "apna", "hamara", "aap", "tum", "hum", "bahut", "thoda", "bada", "chota", "naya", "purana", "sab", "kuch", "koi", "ek", "do", "teen", "char", "paanch"]
)
