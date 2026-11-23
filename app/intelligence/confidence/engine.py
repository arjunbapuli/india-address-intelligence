"""Stage 8: evidence-based confidence and resolution status.

Confidence is computed from measurable evidence only:
  * the ranking score of the chosen candidate (string similarity, match type,
    agreement between locality / city / state / PIN, source reliability);
  * the resolution level reached (locality > PIN > city/district > state);
  * whether a competing candidate in a *different place* scores almost as high;
  * how much the input had to be corrected (STT fixes, fuzzy spelling fixes);
  * explicit uncertainty in the transcript ("somewhere near ...");
  * conflicts (e.g. PIN belongs to another state).

An LLM never sets the confidence. Thresholds are configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings

LEVEL_CAP = {"locality": 0.99, "pincode": 0.88, "city": 0.72, "district": 0.66, "state": 0.42, "none": 0.0}


@dataclass
class ConfidenceInputs:
    level: str
    top_score: float
    runner_up_score: float | None = None
    corrections_transcription: int = 0
    corrections_spelling: int = 0
    min_spelling_similarity: float = 1.0
    uncertainty_markers: int = 0
    pin_conflict: str | None = None  # different_state | different_district | same_district
    pin_consistent: bool = False
    exact_names_only: bool = False


@dataclass
class ConfidenceResult:
    confidence: float
    status: str
    ambiguous: bool
    reasons: list[str] = field(default_factory=list)


def compute_confidence(inp: ConfidenceInputs, settings: Settings) -> ConfidenceResult:
    reasons: list[str] = []
    if inp.level == "none":
        return ConfidenceResult(0.0, "unresolved", False, ["no geographic entity could be matched"])

    conf = inp.top_score
    cap = LEVEL_CAP.get(inp.level, 0.5)
    if inp.level != "locality":
        conf = min(conf, cap)
        reasons.append(f"resolved only to {inp.level} level")

    ambiguous = False
    if inp.runner_up_score is None:
        conf += 0.06
        reasons.append("no competing candidate in another place")
    else:
        margin = inp.top_score - inp.runner_up_score
        if margin < settings.ambiguity_margin:
            ambiguous = True
            conf *= 0.8
            reasons.append(f"competing candidate within {margin:.2f} of the best one")
        elif margin < 2 * settings.ambiguity_margin:
            conf -= 0.03

    if inp.pin_consistent:
        conf += 0.05
        reasons.append("PIN code agrees with locality")
    if inp.pin_conflict == "different_state":
        conf -= 0.30
        reasons.append("PIN code belongs to a different state")
    elif inp.pin_conflict == "different_district":
        conf -= 0.20
        reasons.append("PIN code belongs to a different district")
    elif inp.pin_conflict == "same_district":
        conf -= 0.08
        reasons.append("PIN code is in the same district but a different delivery area")

    if inp.corrections_transcription:
        conf -= min(0.09, 0.03 * inp.corrections_transcription)
        reasons.append(f"{inp.corrections_transcription} speech-to-text correction(s) applied")
    if inp.corrections_spelling:
        conf -= min(0.12, 0.04 * inp.corrections_spelling) + max(0.0, 0.9 - inp.min_spelling_similarity) * 0.5
        reasons.append(f"{inp.corrections_spelling} place name(s) matched approximately")
    if inp.uncertainty_markers:
        conf -= 0.08
        reasons.append("speaker expressed uncertainty")

    conf = max(0.0, min(cap, conf))
    conf = round(conf, 3)

    if ambiguous:
        status = "ambiguous"
    elif conf < settings.confidence_low:
        status = "unresolved"
    elif (
        inp.level == "locality" and conf >= settings.confidence_high and inp.exact_names_only and inp.pin_consistent
        and not inp.corrections_transcription and not inp.corrections_spelling
    ):
        status = "exact_match"
    elif inp.level == "locality" and conf >= settings.confidence_high:
        status = "resolved"
    else:
        status = "partially_resolved"
    return ConfidenceResult(conf, status, ambiguous, reasons)
