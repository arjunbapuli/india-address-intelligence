"""Address validation (POST /v1/address/validate).

Answers, deterministically and with reasons:
  * is the address structurally valid (has the parts a courier needs)?
  * does the PIN exist, and does it match the locality / district / state?
  * is the city-state combination plausible?
  * is the location resolvable to coordinates?
  * which components are missing, and does it need clarification?
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.geography.gazetteer import Gazetteer
from app.intelligence.resolution.models import AddressComponents, Resolution
from app.intelligence.text import compact_key

CheckStatus = Literal["pass", "fail", "warn", "not_applicable"]


class ValidationCheck(BaseModel):
    check: str
    status: CheckStatus
    message: str


class ValidationResult(BaseModel):
    success: bool = True
    is_valid: bool = Field(..., description="True when no check failed and the address is deliverable as given.")
    structurally_valid: bool
    pincode_valid: bool | None
    pincode_matches_locality: bool | None
    pincode_matches_state: bool | None
    city_state_plausible: bool | None
    geolocation_resolvable: bool
    missing_components: list[str]
    needs_clarification: bool
    checks: list[ValidationCheck]
    components: AddressComponents
    resolution: Resolution | None = None


def _norm(s: str | None) -> str:
    return compact_key(s or "")


def _canonical_states(g: Gazetteer, text: str) -> set[str]:
    """'KA'/'Karnataka'/'ಕರ್ನಾಟಕ' -> {'Karnataka'} using gazetteer names and aliases."""
    out = {g.places[h.place_id].name for h in g.lookup_exact(text) if g.places[h.place_id].type == "state"}
    if not out:
        out = {text.strip()}
    return out


def validate_components(g: Gazetteer, comp: AddressComponents, resolution: Resolution | None = None
                        ) -> ValidationResult:
    checks: list[ValidationCheck] = []
    missing: list[str] = []

    has_unit = bool(comp.house_number or comp.unit or comp.plot or comp.building)
    if not has_unit:
        missing.append("house_number")
    if not (comp.locality or comp.sub_locality):
        missing.append("locality")
    if not (comp.city or comp.district):
        missing.append("city")
    if not comp.state:
        missing.append("state")
    if not comp.postal_code:
        missing.append("postal_code")
    structural = has_unit and bool(comp.locality or comp.postal_code) and bool(comp.city or comp.district or comp.postal_code)
    checks.append(ValidationCheck(check="structure", status="pass" if structural else "fail",
                                  message="has unit/house, area and city/PIN" if structural else
                                  "missing: " + ", ".join(missing)))

    pin_valid = pin_loc = pin_state = None
    pin = (comp.postal_code or "").replace(" ", "")
    if pin:
        if not re.fullmatch(r"[1-9]\d{5}", pin):
            pin_valid = False
            checks.append(ValidationCheck(check="pincode_format", status="fail", message="PIN must be 6 digits, not starting with 0"))
        elif pin not in g.pin_place:
            pin_valid = False
            checks.append(ValidationCheck(check="pincode_exists", status="fail", message="PIN not found in India Post directory"))
        else:
            pin_valid = True
            pp = g.places[g.pin_place[pin]]
            checks.append(ValidationCheck(check="pincode_exists", status="pass", message=f"PIN {pin}: {pp.district}, {pp.state}"))
            pin_states = {g.places[lid].state for lid in g.pin_localities.get(pin, [])} | {pp.state}
            if comp.state:
                pin_state = bool(_canonical_states(g, comp.state) & {s for s in pin_states if s})
                checks.append(ValidationCheck(check="pincode_state", status="pass" if pin_state else "fail",
                                              message=f"PIN is in {pp.state}"))
            if comp.locality:
                names = {_norm(g.places[lid].name) for lid in g.pin_localities.get(pin, [])}
                lk = _norm(comp.locality)
                pin_loc = lk in names or any(n.startswith(lk) or lk.startswith(n) for n in names if len(n) >= 4)
                if not pin_loc:
                    # Locality exists elsewhere in the same district -> warn, not fail (PIN areas don't follow
                    # neighbourhood boundaries exactly)
                    same_district = any(
                        g.places[h.place_id].district == pp.district for h in g.lookup_exact(comp.locality))
                    checks.append(ValidationCheck(
                        check="pincode_locality", status="warn" if same_district else "fail",
                        message="locality is served by a different PIN in the same district" if same_district
                        else "locality is not served by this PIN"))
                    if same_district:
                        pin_loc = None
                else:
                    checks.append(ValidationCheck(check="pincode_locality", status="pass",
                                                  message="locality is served by this PIN"))

    city_state = None
    if comp.city and comp.state:
        city_hits = [g.places[h.place_id] for h in g.lookup_exact(comp.city)
                     if g.places[h.place_id].type in ("city", "district")]
        if city_hits:
            city_state = bool({p.state for p in city_hits} & _canonical_states(g, comp.state))
            checks.append(ValidationCheck(check="city_state", status="pass" if city_state else "fail",
                                          message=f"{comp.city} is {'in' if city_state else 'not in'} {comp.state}"))
        else:
            checks.append(ValidationCheck(check="city_state", status="warn",
                                          message=f"city '{comp.city}' not in the curated city list; not checked"))

    geo_ok = bool(resolution and resolution.geolocation.latitude is not None) or bool(pin_valid)
    checks.append(ValidationCheck(check="geolocation", status="pass" if geo_ok else "warn",
                                  message="coordinates available" if geo_ok else "no coordinates could be derived"))

    failed = any(c.status == "fail" for c in checks)
    needs_clar = failed or "house_number" in missing or ("locality" in missing and not pin_valid) or bool(
        resolution and resolution.resolution_status in ("ambiguous", "unresolved"))
    return ValidationResult(
        is_valid=not failed and structural, structurally_valid=structural, pincode_valid=pin_valid,
        pincode_matches_locality=pin_loc, pincode_matches_state=pin_state, city_state_plausible=city_state,
        geolocation_resolvable=geo_ok, missing_components=missing, needs_clarification=needs_clar,
        checks=checks, components=comp, resolution=resolution)
