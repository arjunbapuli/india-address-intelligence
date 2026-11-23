"""Phonetic keys, fuzzy matching on a small gazetteer, post-office name cleaning."""

import pytest

from app.geography.gazetteer import Gazetteer
from app.geography.ingestion.india_post import clean_post_office_name, strip_area_suffix
from app.intelligence.matching.matcher import find_mentions, lookup_window
from app.intelligence.phonetics.indic import indic_phonetic_key, phonetic_similarity
from app.intelligence.text import compact_key, ordinal, smart_title


@pytest.mark.parametrize(
    "a,b",
    [
        ("Koramangala", "Kormangla"),
        ("Bengaluru", "Bangalore"),
        ("Marathahalli", "Marathalli"),
        ("Bhubaneswar", "bhubaneshbar"),
        ("Kolkata", "Calcutta"),
        ("Kukatpally", "kukatpalli"),
        ("Whitefield", "Waitfield"),
    ],
)
def test_phonetic_key_equivalences(a, b):
    assert indic_phonetic_key(a) == indic_phonetic_key(b)


def test_phonetic_key_distinguishes_different_places():
    assert indic_phonetic_key("Koramangala") != indic_phonetic_key("Indiranagar")
    assert phonetic_similarity("Koramangala", "Kormangla") == 1.0
    assert phonetic_similarity("Koramangala", "Jayanagar") < 0.8


def test_text_helpers():
    assert compact_key("Indira Nagar") == compact_key("Indiranagar")
    assert smart_title("BENGALURU URBAN") == "Bengaluru Urban"
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 112)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "112th"]


@pytest.mark.parametrize(
    "raw,name",
    [
        ("Koramangala VI Bk S.O", "Koramangala 6th Block"),
        ("Indiranagar S.O (Bengaluru)", "Indiranagar"),
        ("JP Nagar VIII phase S.O", "JP Nagar 8th Phase"),
        ("E P I P S.O", "EPIP"),
        ("Vadgaon Bk B.O", "Vadgaon Bk"),  # Bk here means Budruk, not Block
        ("ALINAKHIPALEM", "Alinakhipalem"),
        ("Indiranagar Sec14 S.O", "Indiranagar Sector 14"),
    ],
)
def test_clean_post_office_name(raw, name):
    assert clean_post_office_name(raw)[0] == name


def test_strip_area_suffix():
    assert strip_area_suffix("Lajpat Nagar South Delhi", {"South Delhi"}) == "Lajpat Nagar"
    assert strip_area_suffix("Sector 15 Noida", {"Noida"}) is None  # remainder would be generic


@pytest.fixture()
def mini_gaz():
    ents = [
        {"id": 1, "entity_type": "locality", "name": "Koramangala", "state_name": "Karnataka",
         "district_name": "Bengaluru Urban", "pincode": "560034", "latitude": 12.93, "longitude": 77.62,
         "source_key": "india_post", "source_confidence": 0.9, "attributes": {"office_type": "PO", "delivery": True}},
        {"id": 2, "entity_type": "locality", "name": "HSR Layout", "state_name": "Karnataka",
         "district_name": "Bengaluru Urban", "pincode": "560102", "latitude": 12.91, "longitude": 77.64,
         "source_key": "india_post", "source_confidence": 0.9, "attributes": {}},
        {"id": 3, "entity_type": "city", "name": "Bengaluru", "state_name": "Karnataka",
         "district_name": "Bengaluru Urban", "source_key": "curated", "source_confidence": 0.95,
         "attributes": {"districts": ["Bengaluru Urban"], "infer_districts": ["Bengaluru Urban"], "tier": 1}},
        {"id": 4, "entity_type": "state", "name": "Karnataka", "state_name": "Karnataka", "source_key": "india_post"},
        {"id": 5, "entity_type": "pincode", "name": "560102", "state_name": "Karnataka",
         "district_name": "Bengaluru Urban", "pincode": "560102", "source_key": "india_post"},
    ]
    aliases = [{"entity_id": 2, "alias": "HSR", "alias_type": "acronym", "confidence": 0.85},
               {"entity_id": 3, "alias": "Bangalore", "alias_type": "alternate_name", "confidence": 0.95}]
    return Gazetteer.from_records(ents, aliases)


def test_lookup_exact_alias_phonetic_fuzzy(mini_gaz):
    assert lookup_window(mini_gaz, ["koramangala"], False, None)[0] == "exact"
    mt, c = lookup_window(mini_gaz, ["hsr"], False, None)
    assert mt == "exact" and c[0].match_type == "alias" and c[0].place_id == 2
    mt, c = lookup_window(mini_gaz, ["kormangla"], False, None)
    assert mt == "phonetic" and c[0].place_id == 1
    mt, c = lookup_window(mini_gaz, ["koramanagla"], True, None)
    assert mt in ("phonetic", "fuzzy") and c[0].place_id == 1


def test_generic_words_are_never_matched(mini_gaz):
    assert lookup_window(mini_gaz, ["main", "road"], True, None) == ("none", [])


def test_find_mentions_prefers_longest_exact(mini_gaz):
    toks = ["hsr", "layout", "bangalore"]
    mentions, unmatched = find_mentions(mini_gaz, toks, [(0, 3)])
    assert [(m.start, m.end) for m in mentions] == [(0, 2), (2, 3)]
    assert unmatched == []


def test_gazetteer_hierarchy_and_city_inference(mini_gaz):
    p = mini_gaz.places[2]
    assert mini_gaz.city_for_locality(p).name == "Bengaluru"
    assert mini_gaz.pin_localities["560102"] == [2]
    near = mini_gaz.nearby(12.91, 77.64, 1.0)
    assert near and near[0][0].id == 2
    assert "koramangala" in mini_gaz.prefix_keys("kora")
