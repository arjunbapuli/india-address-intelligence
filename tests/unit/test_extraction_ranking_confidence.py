"""Stage 2 extraction (no gazetteer), Stage 6 ranking, Stage 8 confidence."""

from app.core.config import Settings
from app.intelligence.confidence.engine import ConfidenceInputs, compute_confidence
from app.intelligence.extraction.rules import extract
from app.intelligence.normalization.normalizer import normalize
from app.intelligence.ranking.ranker import score


def ex(text: str):
    return extract(normalize(text).tokens, None)


def test_extracts_unit_floor_building_street():
    e = ex("Flat no. B-204, 3rd floor, Prestige Shantiniketan Apartments, ITPL Main Rd")
    assert e.components["unit"] == "B-204"
    assert e.components["floor"] == "3rd Floor"
    assert e.components["building"] == "Prestige Shantiniketan Apartments"
    assert e.components["street"].lower() == "itpl main road"


def test_extracts_main_cross_sector_block_phase():
    e = ex("12, 5th cross, 14th main, sector 2, a block, 7th phase")
    c = e.components
    assert c["house_number"] == "12"
    assert c["cross_road"] == "5th Cross"
    assert c["street"] == "14th Main"
    assert c["sector"] == "Sector 2"
    assert c["block"] == "A Block"
    assert c["phase"] == "7th Phase"


def test_pin_detection_including_split_digits():
    assert ex("bangalore 560 078").pin_candidates == ["560078"]
    assert ex("pin 560102").pin_candidates == ["560102"]


def test_landmarks_pre_and_postposition():
    e = ex("near the old temple, 5th cross")
    assert e.landmarks[0].text == "Old Temple" and e.landmarks[0].generic
    e = ex("Hope Farm ke paas")
    assert e.landmarks[0].text == "Hope Farm" and e.landmarks[0].postposition
    assert not e.landmarks[0].generic


def test_numbered_road_and_gali():
    e = ex("road no 12 , gali number four")
    assert e.components["street"] == "Road No. 12"
    assert e.components["lane"] == "Gali No. 4"


def test_house_number_is_never_invented():
    e = ex("koramangala bangalore")
    assert "house_number" not in e.components


def test_weighted_score_is_transparent():
    w = {"a": 0.5, "b": 0.5}
    assert score({"a": 1.0, "b": 0.0}, w) == 0.5
    # features without a weight are ignored; absent features don't count
    assert score({"a": 1.0}, w) == 1.0


S = Settings(environment="test")


def test_confidence_levels_and_status():
    hi = compute_confidence(ConfidenceInputs(level="locality", top_score=0.95, pin_consistent=True,
                                             exact_names_only=True), S)
    assert hi.status == "exact_match" and hi.confidence >= 0.95
    amb = compute_confidence(ConfidenceInputs(level="locality", top_score=0.8, runner_up_score=0.79), S)
    assert amb.status == "ambiguous" and amb.ambiguous
    city = compute_confidence(ConfidenceInputs(level="city", top_score=0.9), S)
    assert city.status == "partially_resolved" and city.confidence <= 0.72
    none = compute_confidence(ConfidenceInputs(level="none", top_score=0.0), S)
    assert none.status == "unresolved" and none.confidence == 0.0


def test_confidence_penalises_corrections_conflicts_and_uncertainty():
    base = compute_confidence(ConfidenceInputs(level="locality", top_score=0.9), S).confidence
    for kwargs in ({"corrections_transcription": 1}, {"corrections_spelling": 1, "min_spelling_similarity": 0.8},
                   {"uncertainty_markers": 1}, {"pin_conflict": "different_state"}):
        assert compute_confidence(ConfidenceInputs(level="locality", top_score=0.9, **kwargs), S).confidence < base
