"""End-to-end resolution against the full India Post gazetteer (no hand-picked fixtures)."""

import pytest

from app.intelligence.resolution.resolver import Hints


def comp(res):
    return res.components


def test_spec_example_noisy_stt(resolver):
    res = resolver.resolve("14 main hsr sector to near nifty Bangalore")
    c = comp(res)
    assert (c.locality, c.city, c.state, c.street, c.sector) == (
        "HSR Layout", "Bengaluru", "Karnataka", "14th Main", "Sector 2")
    assert c.district == "Bengaluru Urban"
    assert res.component_verification["sector"] == "corrected"
    assert res.component_verification["street"] == "unverified"
    assert res.needs_confirmation  # an STT homophone was corrected
    assert any(x.type == "transcription_fix" for x in res.corrections)
    assert res.geolocation.latitude is not None


def test_conservative_correction_does_not_invent(resolver):
    res = resolver.resolve("Near the old temple, fifth cross, somewhere in Rajajinagar")
    c = comp(res)
    assert c.cross_road == "5th Cross"
    assert c.house_number is None and c.street is None
    assert c.landmark == "Old Temple" and res.component_verification["landmark"] == "unverified"
    assert res.resolution_status == "ambiguous"  # several Rajajinagars in India
    assert c.postal_code is None  # no PIN inferred for an ambiguous match
    assert res.needs_confirmation and res.needs_clarification
    assert any("Rajajinagar" in q.question for q in res.clarification_questions)


def test_city_hint_disambiguates(resolver):
    res = resolver.resolve("Rajajinagar 5th cross", Hints(city="Bengaluru"))
    assert comp(res).district == "Bengaluru Urban"
    assert res.resolution_status in ("resolved", "exact_match")


@pytest.mark.parametrize(
    "text,locality,state",
    [
        ("Whitefield, Hope Farm ke paas", "Whitefield", "Karnataka"),
        ("kormangla 5th block bangalore 560095", "Koramangala", "Karnataka"),
        ("Andheri West Mumbai 400053", "Andheri West", "Maharashtra"),
        ("Banjara hills road no 12 hyderabad", "Banjara Hills", "Telangana"),
        ("B-12 Lajpat Nagar 2 New Delhi", "Lajpat Nagar 2", "Delhi"),
        ("Salt Lake sector 5 Kolkata", "Bidhan Nagar", "West Bengal"),
        ("velachery chennai", "Velacheri", "Tamil Nadu"),
        ("इंदिरानगर बेंगलुरु", "Indiranagar", "Karnataka"),
        ("Gomti Nagar, Lucknow", "Gomti Nagar", "Uttar Pradesh"),
        ("navrangpura ahmedabad", "Navrangpura", "Gujarat"),
    ],
)
def test_resolves_localities_across_india(resolver, text, locality, state):
    res = resolver.resolve(text)
    assert comp(res).locality == locality, res.normalized_address
    assert comp(res).state == state


def test_pin_only_resolves_to_pincode_level(resolver):
    res = resolver.resolve("five six zero zero three four")
    assert res.resolution_level == "pincode"
    assert comp(res).postal_code == "560034" and comp(res).city == "Bengaluru"
    assert comp(res).locality is None


def test_pin_conflict_is_flagged_not_silently_fixed(resolver):
    res = resolver.resolve("HSR Layout Bangalore 110001")  # Delhi PIN
    assert res.component_verification["postal_code"] == "conflict"
    assert comp(res).postal_code == "110001"  # user's value preserved
    assert res.needs_confirmation
    assert any("does not match" in w for w in res.warnings)


def test_locality_contradicting_city_falls_back_to_city(resolver):
    res = resolver.resolve("banjara bengaluru")  # 'Banjara' exists only in Telangana
    assert res.resolution_level == "city"
    assert comp(res).city == "Bengaluru" and comp(res).locality is None
    assert res.alternatives  # the contradicting match is offered, not chosen


def test_unresolvable_input(resolver):
    res = resolver.resolve("घर नंबर 25, तीसरा क्रॉस")
    assert res.resolution_status == "unresolved"
    assert comp(res).house_number == "25" and comp(res).cross_road == "3rd Cross"
    assert "locality" in res.missing_components


def test_district_alias_does_not_beat_locality(resolver):
    res = resolver.resolve("Near temple, Vijay Nagar, gali number four")
    assert comp(res).locality and "Vijay" in comp(res).locality
    assert res.resolution_status == "ambiguous"
    assert comp(res).lane == "Gali No. 4"


def test_city_level_old_names(resolver):
    for name, city in (("Gurgaon", "Gurugram"), ("Bombay", "Mumbai"), ("Calcutta", "Kolkata"), ("Madras", "Chennai")):
        res = resolver.resolve(f"sector 1, {name}")
        assert comp(res).city == city, name
