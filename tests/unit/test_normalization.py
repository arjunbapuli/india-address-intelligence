"""Stage 1: numbers, ordinals, abbreviations, Hinglish, transliteration, STT homophones."""

import pytest

from app.intelligence.normalization.normalizer import normalize
from app.intelligence.normalization.numbers import (
    indic_digits_to_ascii,
    parse_number_words,
    roman_to_int,
    spoken_digit_run,
)
from app.intelligence.normalization.transliterate import detect_scripts, to_latin


@pytest.mark.parametrize(
    "words,expected",
    [
        ("fourteen", (14, 1, False)),
        ("twenty one", (21, 2, False)),
        ("twenty first", (21, 2, True)),
        ("one hundred and five", (105, 4, False)),
        ("third", (3, 1, True)),
        ("teesra", (3, 1, True)),
        ("main", (None, 0, False)),
    ],
)
def test_parse_number_words(words, expected):
    assert parse_number_words(words.split(), 0) == expected


def test_hindi_cardinals_only_in_slot_context():
    # "bara" (12) is also a place word ("Bara Bazar"); only parsed when allowed
    assert parse_number_words(["bara"], 0) == (None, 0, False)
    assert parse_number_words(["bara"], 0, allow_hindi=True)[0] == 12


def test_spoken_digits_with_double():
    assert spoken_digit_run(["five", "six", "double", "zero", "three", "four"], 0) == ("560034", 6)
    assert spoken_digit_run(["five", "six"], 0) == (None, 0)  # too short to be a code


def test_indic_digits_and_roman():
    assert indic_digits_to_ascii("घर २५, ೫೬೦೧೦೨") == "घर 25, 560102"
    assert roman_to_int("viii") == 8
    assert roman_to_int("block") is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("14 main hsr sector to near nifty Bangalore", "14th main hsr sector 2 near nifty bangalore"),
        ("third cross", "3rd cross"),
        ("phase one", "phase 1"),
        ("h.no 12/3, 5th x, jp nagar 7th ph", "house no 12/3 , 5th cross , jp nagar 7th phase"),
        ("Opp. Forum Mall, Hosur Rd", "opposite forum mall , hosur road"),
        ("five six double zero three four", "560034"),
        ("pehli manzil, gali no teen", "1st floor , gali no 3"),
    ],
)
def test_normalize_examples(raw, expected):
    assert normalize(raw).text == expected


def test_original_is_preserved_and_corrections_recorded():
    raw = "Mera address hai fourteen main HSR sector two"
    out = normalize(raw)
    assert out.original == raw
    types = {c.type for c in out.corrections}
    assert {"filler_removal", "number_normalization"} <= types


def test_stt_homophone_is_a_transcription_fix_only_in_numeric_slot():
    out = normalize("sector to")
    assert out.text == "sector 2"
    assert any(c.type == "transcription_fix" for c in out.corrections)
    # "to" elsewhere is untouched
    assert normalize("deliver near the road to the market").text.endswith("road to the market")


def test_hinglish_postposition_marker():
    out = normalize("Whitefield, Hope Farm ke paas")
    assert out.tokens[-1] == "_nearpost_"


def test_uncertainty_markers_detected_and_removed():
    out = normalize("near the old temple, somewhere in Rajajinagar")
    assert out.uncertainty_markers == ["somewhere in"]
    assert "somewhere" not in out.text


def test_transliteration_devanagari_and_dravidian():
    assert to_latin("इंदिरानगर")[0] == "indiranagar"
    assert to_latin("ಬೆಂಗಳೂರು")[0] == "bengaluru"
    assert to_latin("அண்ணா நகர்")[0].startswith("anna nag")
    assert detect_scripts("HSR लेआउट ಬೆಂಗಳೂರು") == {"hi", "kn"}


def test_devanagari_address_words_become_english():
    out = normalize("घर नंबर 25, तीसरा क्रॉस")
    assert out.text == "house no 25 , 3rd cross"
    assert "hi" in out.languages
