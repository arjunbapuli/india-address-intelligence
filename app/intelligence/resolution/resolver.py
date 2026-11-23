"""The address resolution pipeline (Stages 1-8) in one place.

    raw text
      -> normalize()            Stage 1  form only, every change recorded
      -> extract()              Stage 2  house/flat/floor/street/cross/sector/landmark
      -> find_mentions()        Stage 3/5  gazetteer: exact > alias > phonetic > fuzzy
      -> admin context          city / district / state / PIN evidence (+ request hints)
      -> features + rank        Stage 6  transparent weighted score
      -> components + format    Stage 7  never invents house numbers, streets, PINs
      -> confidence + status    Stage 8  evidence-based, configurable thresholds

Conservative-correction rules implemented here:
  * Formatting/abbreviation fixes are applied silently but listed in `corrections`.
  * STT homophone fixes ("sector to" -> "Sector 2") and fuzzy spelling fixes
    always set needs_confirmation.
  * A PIN is only filled in when given and consistent, or when the matched
    locality maps to exactly one PIN in its district (marked "inferred").
  * Anything we cannot verify is kept as given and marked "unverified".
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from app.core.config import Settings, get_settings
from app.geography.gazetteer import Gazetteer, Place, haversine_km
from app.intelligence.confidence.engine import ConfidenceInputs, compute_confidence
from app.intelligence.extraction.rules import Extraction, LandmarkMention, Span, extract, landmark_from_span
from app.intelligence.matching.matcher import ADMIN, LOCAL, Candidate, Mention, find_mentions, lookup_window
from app.intelligence.normalization.normalizer import Correction as NCorrection
from app.intelligence.normalization.normalizer import NormalizedInput, normalize
from app.intelligence.normalization.vocabulary import COMMON_WORDS
from app.intelligence.ranking.ranker import (
    AdminContext,
    ScoredCandidate,
    distinct_places,
    features_for,
    pin_in_name_family,
    rank,
    score,
)
from app.intelligence.resolution import models as M
from app.intelligence.text import compact_key, smart_title
from app.version import RESOLVER_VERSION

UNVERIFIABLE_COMPONENTS = ("house_number", "unit", "floor", "building", "plot", "street", "cross_road", "lane",
                           "block", "sector", "phase", "stage", "pocket")


@dataclass
class Hints:
    city: str | None = None
    state: str | None = None
    pincode: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    language: str | None = None


@dataclass
class _AdminMention:
    mention: Mention
    places: list[tuple[Place, Candidate]] = field(default_factory=list)


class AddressResolver:
    def __init__(self, gazetteer: Gazetteer, settings: Settings | None = None, llm_extractor=None,
                 geo_fallback=None) -> None:
        self.g = gazetteer
        self.s = settings or get_settings()
        self.llm = llm_extractor
        self.geo_fallback = geo_fallback

    # ================================================================== API
    def resolve(self, text: str, hints: Hints | None = None) -> M.Resolution:
        t0 = time.perf_counter()
        hints = hints or Hints()
        g = self.g
        nz = normalize(text)
        ex = extract(nz.tokens, g)
        corrections = [self._corr(c) for c in nz.corrections]
        warnings: list[str] = []
        llm_used = False

        self._peel_landmarks(nz.tokens, ex)

        spans = [(s.start, s.end) for s in ex.claimed_spans] + [(s.start, s.end) for s in ex.free_spans]
        mentions, unmatched = find_mentions(g, nz.tokens, spans, allow_fuzzy=False)

        mentions, dropped = self._apply_admin_markers(mentions, ex)
        unmatched = sorted(unmatched + dropped)
        ctx, admin_mentions = self._admin_context(mentions, ex, hints, warnings)
        # Fuzzy pass on whatever exact/alias/phonetic lookup could not explain, scoped by admin evidence
        scope = g.keys_for_places(g.localities_in_districts(ctx.scope_districts)) if ctx.scope_districts else None
        if unmatched:
            more, unmatched = find_mentions(g, nz.tokens, unmatched, allow_fuzzy=True, scope_keys=scope)
            more, dropped_more = self._apply_admin_markers(more, ex)
            unmatched = sorted(unmatched + dropped_more)
            mentions = sorted(mentions + more, key=lambda m: m.start)
            if more:
                ctx, admin_mentions = self._admin_context(mentions, ex, hints, warnings)

        locality_mentions = [m for m in mentions if not self._is_admin_mention(m)]
        # "vaishali ghaziabad", "bodh gaya bihar": a name that is a district/city elsewhere but a locality
        # inside the place the user also named is read as the locality.
        demoted = self._demote_admin_mentions(mentions, admin_mentions)
        if demoted:
            locality_mentions = sorted(locality_mentions + demoted, key=lambda m: m.start)
            ctx, admin_mentions = self._admin_context([m for m in mentions if m not in demoted], ex, hints, warnings)
            scope = g.keys_for_places(g.localities_in_districts(ctx.scope_districts)) if ctx.scope_districts else None
        # An exact name match elsewhere in India must not hide an approximate match inside the city
        # the user named ("indra nagar bangalore": exact 'Indra Nagar' exists in other states).
        if scope:
            scope_ids = {pid for ids in scope.values() for pid in ids}
            for m in locality_mentions:
                if m.start < 0 or any(c.place_id in scope_ids for c in m.candidates):
                    continue
                words = nz.tokens[m.start:m.end]
                _, extra = lookup_window(g, words, allow_fuzzy=True, scope_keys=scope, skip_exact=True)
                m.candidates.extend(c for c in extra if c.place_id in scope_ids)

        # Landmark text as a last resort locality source ("near Koramangala")
        from_landmark = False
        if not locality_mentions and ex.landmarks:
            lm_spans = [(lm.span.start, lm.span.end) for lm in ex.landmarks if not lm.generic]
            lm_mentions, _ = find_mentions(g, nz.tokens, lm_spans, allow_fuzzy=bool(scope), scope_keys=scope)
            locality_mentions = [m for m in lm_mentions if not self._is_admin_mention(m)]
            from_landmark = bool(locality_mentions)

        # Optional LLM assist: only proposes strings; the gazetteer decides.
        if self.llm is not None and self._should_call_llm(locality_mentions, admin_mentions):
            llm_mentions, llm_used = self._llm_mentions(text, scope, warnings)
            if llm_mentions:
                locality_mentions = [m for m in llm_mentions if not self._is_admin_mention(m)] or locality_mentions
                if not admin_mentions:
                    extra = [m for m in llm_mentions if self._is_admin_mention(m)]
                    if extra:
                        ctx, admin_mentions = self._admin_context(mentions + extra, ex, hints, warnings)

        scored = self._score_localities(locality_mentions, ctx, ex.landmarks, from_landmark)
        ranked = distinct_places(rank(g, scored))

        top = ranked[0] if ranked else None
        # Never return a locality that contradicts the city/district the user said; fall back to the
        # city level and show the contradicting matches as alternatives instead.
        if top and (top.features.get("city_consistency", 1) < 0.1 or top.features.get("state_consistency", 1) == 0.0):
            warnings.append(f"'{top.mention_text}' matched {top.place.name} ({top.place.district}), which is not in "
                            f"the city/state mentioned; resolved at city level only")
            top = None

        unparsed = [" ".join(nz.tokens[a:b]) for a, b in unmatched
                    if not all(t in ("near", "the", "of", "in", "at") for t in nz.tokens[a:b])]
        result = self._assemble(text, nz, ex, ctx, admin_mentions, ranked, top, corrections, warnings, unparsed,
                                from_landmark, hints)
        result.metadata.llm_used = llm_used
        if self.geo_fallback is not None and result.resolution_level == "locality" and result.normalized_address:
            r = self.geo_fallback.refine(result.normalized_address, result.geolocation.latitude,
                                         result.geolocation.longitude, result.components.postal_code)
            if r is not None:
                result.geolocation = M.Geolocation(
                    latitude=r.latitude, longitude=r.longitude, accuracy="external_geocoder", source=r.provider,
                    accuracy_note="Geocoded by an external provider and checked to lie within the resolved area.")
                result.metadata.external_geocoder_used = True
        result.metadata.processing_time_ms = round((time.perf_counter() - t0) * 1000, 2)
        return result

    # ============================================================ helpers
    @staticmethod
    def _corr(c: NCorrection, stage: str = "normalization") -> M.Correction:
        return M.Correction(original=c.original, corrected=c.corrected, type=c.type, reason=c.reason, stage=stage)

    def _peel_landmarks(self, tokens: list[str], ex: Extraction) -> None:
        """'near nifty bangalore' -> landmark 'Nifty' + place 'bangalore';
        'whitefield hope farm ke paas' -> place 'whitefield' + landmark 'Hope Farm'."""
        g = self.g
        new_landmarks: list[LandmarkMention] = []
        for lm in ex.landmarks:
            s, e = lm.span.start, lm.span.end
            changed = True
            while changed and e - s > 1:
                changed = False
                for k in (3, 2, 1):
                    if e - s <= k:
                        continue
                    if lm.postposition:
                        a, b = s, s + k
                    else:
                        a, b = e - k, e
                    words = tokens[a:b]
                    mt, cands = lookup_window(g, words, allow_fuzzy=False, scope_keys=None)
                    good = [c for c in cands if g.places[c.place_id].type in ADMIN
                            or (g.places[c.place_id].type in LOCAL and c.match_type == "exact" and len(compact_key(" ".join(words))) >= 5)]
                    if good:
                        ex.free_spans.append(Span(a, b))
                        if lm.postposition:
                            s = b
                        else:
                            e = a
                        changed = True
                        break
            if (s, e) != (lm.span.start, lm.span.end):
                new_landmarks.append(landmark_from_span(tokens, Span(s, e), lm.relation, lm.postposition))
            else:
                new_landmarks.append(lm)
        ex.landmarks = new_landmarks

    def _is_admin_mention(self, m: Mention) -> bool:
        g = self.g
        admin = [c for c in m.candidates if g.places[c.place_id].type in ADMIN]
        if not admin:
            return False
        local_exact = [c for c in m.candidates if g.places[c.place_id].type in LOCAL and c.match_type == "exact"]
        if any(c.match_type == "exact" for c in admin):
            return True
        if any(c.match_type == "alias" and g.places[c.place_id].type == "city" for c in admin):
            return True  # curated city names (Bombay, Gurgaon, Calcutta) beat same-named villages
        if any(c.match_type == "alias" for c in admin):
            # 'Vijay Nagar' is an alias of a district but the exact name of many localities: prefer localities
            return not local_exact
        best_admin = max(c.similarity for c in admin)
        local = [c for c in m.candidates if g.places[c.place_id].type in LOCAL]
        best_local = max((c.similarity for c in local), default=0.0)
        return best_admin >= 0.85 and best_admin >= best_local

    def _apply_admin_markers(self, mentions: list[Mention], ex: Extraction
                             ) -> tuple[list[Mention], list[tuple[int, int]]]:
        """'village X' must be a locality reading of X; 'district Y' must be a district reading of Y.
        Returns (kept mentions, spans of village names we could not verify — preserved as unparsed text)."""
        if not ex.admin_markers:
            return mentions, []
        g = self.g
        out = []
        dropped: list[tuple[int, int]] = []
        for m in mentions:
            kind = next((k for k, sp in ex.admin_markers if sp.start <= m.start and m.end <= sp.end), None)
            if kind in ("village", "post_office"):
                m.candidates = [c for c in m.candidates if g.places[c.place_id].type in LOCAL]
            elif kind == "district":
                m.candidates = [c for c in m.candidates if g.places[c.place_id].type == "district"] or m.candidates
            if m.candidates:
                out.append(m)
            else:
                dropped.append((m.start, m.end))
        return out, dropped

    def _demote_admin_mentions(self, mentions: list[Mention], admin_mentions: list[_AdminMention]) -> list[Mention]:
        if len(admin_mentions) < 2:
            return []
        g = self.g
        out = []
        for am in admin_mentions:
            m = am.mention
            others = [o for o in admin_mentions if o is not am]
            scope_states, scope_districts = set(), set()
            for o in others:
                for p, _ in o.places:
                    scope_states.add(p.state)
                    if p.type == "city" and p.id in g.cities:
                        scope_districts.update((g.cities[p.id].state, d) for d in g.cities[p.id].districts)
                    elif p.type == "district":
                        scope_districts.add((p.state, p.name))
            # Is the admin reading itself consistent with the others? Then keep it ("Pune, Maharashtra").
            if any(p.state in scope_states and (not scope_districts or p.type == "state"
                   or (p.state, p.district) in scope_districts or (p.state, p.name) in scope_districts)
                   for p, _ in am.places):
                continue
            local = [c for c in m.candidates if g.places[c.place_id].type in LOCAL and c.match_type == "exact"]
            fits = [c for c in local if (g.places[c.place_id].state, g.places[c.place_id].district) in scope_districts
                    or (not scope_districts and g.places[c.place_id].state in scope_states)]
            if fits:
                m.candidates = fits
                out.append(m)
        return out

    def _admin_context(self, mentions: list[Mention], ex: Extraction, hints: Hints, warnings: list[str]
                       ) -> tuple[AdminContext, list[_AdminMention]]:
        g = self.g
        ctx = AdminContext()
        admin_mentions: list[_AdminMention] = []
        for m in mentions:
            if not self._is_admin_mention(m):
                continue
            am = _AdminMention(m)
            for c in m.candidates:
                p = g.places[c.place_id]
                if p.type in ADMIN:
                    am.places.append((p, c))
            admin_mentions.append(am)

        # PIN: explicit in text first, then the request hint
        pins = list(ex.pin_candidates) + ([hints.pincode] if hints.pincode else [])
        for pin in pins:
            if not re.fullmatch(r"[1-9]\d{5}", pin or ""):
                continue
            ctx.pincode = pin
            pid = g.pin_place.get(pin)
            if pid is not None:
                ctx.pin_valid = True
                ctx.pin_place = g.places[pid]
                ctx.pin_districts = {(g.places[lid].state, g.places[lid].district) for lid in g.pin_localities.get(pin, [])
                                     if g.places[lid].district}
                if not ctx.pin_districts and ctx.pin_place.district:
                    ctx.pin_districts = {(ctx.pin_place.state, ctx.pin_place.district)}
                break
        if ctx.pincode and not ctx.pin_valid:
            warnings.append("PIN code not found in the India Post directory")

        # Resolve multi-valued admin mentions ("Aurangabad" is in Bihar and Maharashtra) using other evidence
        explicit_states = {p.name for am in admin_mentions for p, _ in am.places if p.type == "state"}
        for am in admin_mentions:
            places = [(p, c) for p, c in am.places if p.type != "state"]
            if not places:
                continue
            if explicit_states:
                filtered = [(p, c) for p, c in places if p.state in explicit_states]
                places = filtered or places
            if ctx.pin_place is not None and ctx.pin_place.state:
                filtered = [(p, c) for p, c in places if p.state == ctx.pin_place.state]
                places = filtered or places
            if any(p.type == "city" for p, _ in places):
                places = [(p, c) for p, c in places if p.type == "city"]
            for p, _c in places:
                if p.type == "city":
                    ctx.city_ids.add(p.id)
                    info = g.cities.get(p.id)
                    if info:
                        ctx.city_districts.update((info.state, d) for d in info.districts)
                elif p.type == "district" and p.state:
                    ctx.districts.add((p.state, p.name))
        ctx.states = set(explicit_states)

        # Request hints: used as context unless they contradict what was said
        if hints.city and not ctx.city_ids and not ctx.districts:
            for h in g.lookup_exact(hints.city):
                p = g.places[h.place_id]
                if p.type == "city" and (not ctx.states or p.state in ctx.states):
                    ctx.city_ids.add(p.id)
                    ctx.city_districts.update((p.state, d) for d in g.cities[p.id].districts)
                elif p.type == "district" and (not ctx.states or p.state in ctx.states):
                    ctx.districts.add((p.state, p.name))
        if hints.state and not ctx.states:
            for h in g.lookup_exact(hints.state):
                p = g.places[h.place_id]
                if p.type == "state":
                    ctx.states.add(p.name)
        ctx.near_lat, ctx.near_lon = hints.latitude, hints.longitude
        return ctx, admin_mentions

    def _should_call_llm(self, locality_mentions: list[Mention], admin_mentions: list[_AdminMention]) -> bool:
        if not self.s.llm_send_address_data or self.s.llm_mode == "off":
            return False
        if self.s.llm_mode == "always":
            return True
        return not locality_mentions

    def _llm_mentions(self, text: str, scope, warnings: list[str]) -> tuple[list[Mention], bool]:
        try:
            proposal = self.llm.extract(text)
        except Exception as exc:  # LLM failures must never fail the request
            warnings.append(f"LLM extraction failed ({type(exc).__name__}); used deterministic pipeline only")
            return [], False
        if not proposal:
            return [], True
        mentions: list[Mention] = []
        for field_name in ("locality", "sub_locality", "city", "district", "state"):
            val = proposal.get(field_name)
            if not val or not isinstance(val, str):
                continue
            words = normalize(val).tokens
            words = [w for w in words if w != ","]
            if not words:
                continue
            _, cands = lookup_window(self.g, words, allow_fuzzy=True, scope_keys=scope)
            for c in cands:
                c.similarity = round(c.similarity * 0.9, 3)  # LLM-proposed text is weaker evidence
            if cands:
                mentions.append(Mention(-1, -1, f"[llm] {val}", cands))
        return mentions, True

    def _match_landmark(self, p: Place, landmarks: list[LandmarkMention], radius_km: float = 3.0
                        ) -> tuple[Place, LandmarkMention, str, float] | None:
        """Find a known landmark (OSM data) near candidate `p` matching what the user said.
        Exact name/alias > phonetic > fuzzy; only landmarks within `radius_km` count."""
        g = self.g
        if not landmarks or p.lat is None or p.lon is None or not g.stats.entities.get("landmark"):
            return None
        best = None
        for lm in landmarks:
            if lm.generic or not lm.text:
                continue
            words = lm.text.lower().split()
            mt, cands = lookup_window(g, words, allow_fuzzy=True, scope_keys=None, fuzzy_cutoff_global=85.0)
            for c in cands:
                lp = g.places[c.place_id]
                if lp.type != "landmark" or lp.lat is None or lp.lon is None:
                    continue
                if haversine_km(p.lat, p.lon, lp.lat, lp.lon) > radius_km:
                    continue
                if best is None or c.similarity > best[3]:
                    best = (lp, lm, c.match_type, c.similarity)
        return best

    def _landmark_support(self, p: Place, landmarks: list[LandmarkMention]) -> float | None:
        """1.0 if a named landmark the user mentioned exists near the candidate (needs OSM landmark data)."""
        return 1.0 if self._match_landmark(p, landmarks) else None

    def _score_localities(self, mentions: list[Mention], ctx: AdminContext, landmarks: list[LandmarkMention],
                          from_landmark: bool) -> list[ScoredCandidate]:
        g = self.g
        weights = self.s.ranking_weights
        out: list[ScoredCandidate] = []
        for m in mentions:
            cands = [c for c in m.candidates if g.places[c.place_id].type in LOCAL]
            # Prefer candidates inside the admin scope when there are very many (e.g. 'Gandhi Nagar')
            if len(cands) > 80 and ctx.scope_districts:
                inscope = [c for c in cands if (g.places[c.place_id].state, g.places[c.place_id].district) in ctx.scope_districts]
                cands = inscope or cands[:80]
            for c in cands[:400]:
                p = g.places[c.place_id]
                if from_landmark:
                    c = Candidate(c.place_id, c.match_type, round(c.similarity * 0.85, 3), c.alias_type, c.matched_key)
                f = features_for(g, p, c, ctx, self._landmark_support(p, landmarks))
                out.append(ScoredCandidate(p, c, m.text, f, score(f, weights)))
        return out

    # ========================================================== assembly
    def _assemble(self, text: str, nz: NormalizedInput, ex: Extraction, ctx: AdminContext,
                  admin_mentions: list[_AdminMention], ranked: list[ScoredCandidate], top: ScoredCandidate | None,
                  corrections: list[M.Correction], warnings: list[str], unparsed: list[str], from_landmark: bool,
                  hints: Hints) -> M.Resolution:
        g, s = self.g, self.s
        comp = M.AddressComponents()
        ver: dict[str, str] = {}
        for k, v in ex.components.items():
            if hasattr(comp, k) and v:
                setattr(comp, k, v)
                ver[k] = "unverified"
        for corr in corrections:
            if corr.type == "transcription_fix":
                for k in ("sector", "phase", "block", "stage", "floor", "lane", "pocket", "house_number"):
                    v = getattr(comp, k)
                    if v and corr.corrected.split()[-1] in v.split():
                        ver[k] = "corrected"

        level = "none"
        conf_in = ConfidenceInputs(level="none", top_score=0.0)
        n_spelling = 0
        min_sim = 1.0
        exact_only = True
        alternatives: list[M.Candidate] = []
        evidence = M.RankingEvidence(weights=s.ranking_weights)
        city_name: str | None = None

        # ---- choose admin entities (city / district / state) mentioned
        mentioned_city = None
        mentioned_district = None
        mentioned_state = None
        for am in admin_mentions:
            has_city = any(p.type == "city" for p, _ in am.places)
            for p, c in am.places:
                if has_city and p.type == "district":
                    continue  # 'Bangalore' names the city; the district is implied, not mentioned
                if top is not None:
                    tp = top.place
                    consistent = (
                        (p.type == "state" and p.name == tp.state)
                        or (p.type == "district" and p.state == tp.state and p.name == tp.district)
                        or (p.type == "city" and (tp.state, tp.district) in {(g.cities[p.id].state, d) for d in g.cities[p.id].districts})
                    )
                    if not consistent:
                        continue
                if p.type == "city" and mentioned_city is None:
                    mentioned_city = (p, c, am.mention)
                elif p.type == "district" and mentioned_district is None:
                    mentioned_district = (p, c, am.mention)
                elif p.type == "state" and mentioned_state is None:
                    mentioned_state = (p, c, am.mention)
            # record alias expansions / spelling fixes for admin entities actually used
        for item in (mentioned_city, mentioned_district, mentioned_state):
            if item is None:
                continue
            p, c, m = item
            if c.match_type == "alias" and compact_key(m.text) != compact_key(p.name):
                corrections.append(M.Correction(original=m.text, corrected=p.name, type="entity_expansion",
                                                reason=f"known alternate name ({c.alias_type})", stage="matching"))
            elif c.match_type in ("fuzzy", "phonetic"):
                corrections.append(M.Correction(original=m.text, corrected=p.name, type="spelling_correction",
                                                reason=f"{c.match_type} match (similarity {c.similarity:.2f})",
                                                stage="matching"))
                n_spelling += 1
                min_sim = min(min_sim, c.similarity)
                exact_only = False

        # ---- locality-level resolution
        if top is not None:
            p = top.place
            level = "locality"
            comp.locality = p.name
            ver["locality"] = "verified" if top.candidate.match_type in ("exact", "alias") else "corrected"
            if top.candidate.match_type == "alias" and compact_key(top.mention_text) != compact_key(p.name):
                corrections.append(M.Correction(original=top.mention_text, corrected=p.name, type="entity_expansion",
                                                reason=f"known alternate name ({top.candidate.alias_type})",
                                                stage="matching"))
            elif top.candidate.match_type in ("fuzzy", "phonetic"):
                corrections.append(M.Correction(original=top.mention_text, corrected=p.name, type="spelling_correction",
                                                reason=f"{top.candidate.match_type} match to known locality "
                                                       f"(similarity {top.candidate.similarity:.2f})", stage="matching"))
                n_spelling += 1
                min_sim = min(min_sim, top.candidate.similarity)
                exact_only = False
            suffix = self._locality_suffix(nz.tokens, top, unparsed)
            if suffix:
                comp.locality = f"{p.name} {suffix}"
            if from_landmark:
                warnings.append("locality inferred from a landmark phrase ('near ...'); please confirm")
            comp.district = p.district
            comp.state = p.state
            ver["district"] = "verified" if mentioned_district else "inferred"
            ver["state"] = "verified" if mentioned_state else "inferred"
            if mentioned_city:
                city_name = mentioned_city[0].name
                ver["city"] = "verified"
            else:
                hinted = next((g.places[cid] for cid in ctx.city_ids), None)
                info = g.city_for_locality(p)
                if hinted is not None and (p.state, p.district) in {(g.cities[hinted.id].state, d) for d in g.cities[hinted.id].districts}:
                    city_name = hinted.name
                    ver["city"] = "verified"
                elif info is not None:
                    city_name = info.name
                    ver["city"] = "inferred"
            if city_name is None:
                # City said in the input but absorbed into a longer place name ("Model Town Ludhiana")
                said = compact_key(text)
                for cid in g.city_by_district.get((p.state, p.district), []):
                    info = g.cities[cid]
                    if len(compact_key(info.name)) >= 4 and compact_key(info.name) in said:
                        city_name = info.name
                        ver["city"] = "verified"
                        break
            comp.city = city_name

            # Secondary locality mention in the same district -> sub_locality
            for other in ranked[1:]:
                op = other.place
                if (other.mention_text != top.mention_text and op.district == p.district and op.state == p.state
                        and other.candidate.match_type in ("exact", "alias") and op.name != p.name):
                    comp.sub_locality = op.name
                    ver["sub_locality"] = "verified"
                    break

            competitor = self._runner_up(ranked, top)
            is_ambiguous = competitor is not None and top.score - competitor.score < s.ambiguity_margin

            # PIN
            pin_conflict = None
            if ctx.pincode:
                comp.postal_code = ctx.pincode
                if not ctx.pin_valid:
                    ver["postal_code"] = "conflict"
                elif p.pincode == ctx.pincode or pin_in_name_family(g, ctx.pincode, p):
                    ver["postal_code"] = "verified"
                    conf_in.pin_consistent = True
                else:
                    ver["postal_code"] = "conflict"
                    pin_place = ctx.pin_place
                    if pin_place and pin_place.state != p.state:
                        pin_conflict = "different_state"
                    elif (p.state, p.district) not in ctx.pin_districts:
                        pin_conflict = "different_district"
                    else:
                        pin_conflict = "same_district"
                    warnings.append(f"PIN {ctx.pincode} does not match {p.name} (India Post lists {p.pincode})")
            elif p.pincode and p.source == "india_post" and not is_ambiguous and self._pin_unambiguous(p):
                comp.postal_code = p.pincode
                ver["postal_code"] = "inferred"
                corrections.append(M.Correction(original="", corrected=p.pincode, type="inference",
                                                reason=f"PIN of the only post office named {p.name} in {p.district}",
                                                stage="resolution"))
            conf_in.pin_conflict = pin_conflict
            conf_in.level = "locality"
            conf_in.top_score = top.score
            evidence.features = {k: round(v, 3) for k, v in top.features.items()}
            evidence.score = round(top.score, 3)

            conf_in.runner_up_score = competitor.score if competitor else None
            for alt in ranked[1:1 + s.max_alternatives]:
                alternatives.append(self._candidate_model(alt))

        # ---- no locality: fall back to PIN / city / district / state
        else:
            if ctx.pin_valid and ctx.pin_place is not None:
                level = "pincode"
                pp = ctx.pin_place
                comp.postal_code = ctx.pincode
                comp.district, comp.state = pp.district, pp.state
                ver.update({"postal_code": "verified", "district": "inferred", "state": "inferred"})
                if mentioned_state and mentioned_state[0].name != pp.state:
                    ver["state"] = "conflict"
                    warnings.append(f"PIN {ctx.pincode} is in {pp.state}, not {mentioned_state[0].name}")
                    conf_in.pin_conflict = "different_state"
                if mentioned_city:
                    info = g.cities[mentioned_city[0].id]
                    if (pp.state, pp.district) in {(info.state, d) for d in info.districts}:
                        comp.city = info.name
                        ver["city"] = "verified"
                    else:
                        warnings.append(f"PIN {ctx.pincode} is not in {info.name}")
                        conf_in.pin_conflict = conf_in.pin_conflict or "different_district"
                elif (cinfo := g.cities.get(g.infer_city_for_district.get((pp.state, pp.district), -1))) is not None:
                    comp.city = cinfo.name
                    ver["city"] = "inferred"
                conf_in.level = "pincode"
                conf_in.top_score = 0.95
            elif mentioned_city or mentioned_district:
                item = mentioned_city or mentioned_district
                assert item is not None
                p, c, _ = item
                level = "city" if mentioned_city else "district"
                if mentioned_city:
                    comp.city = p.name
                    ver["city"] = "verified" if c.match_type in ("exact", "alias") else "corrected"
                    info = g.cities[p.id]
                    if len(info.districts) == 1:
                        comp.district = info.districts[0]
                        ver["district"] = "inferred"
                else:
                    comp.district = p.name
                    ver["district"] = "verified" if c.match_type in ("exact", "alias") else "corrected"
                comp.state = p.state
                ver["state"] = "verified" if mentioned_state else "inferred"
                if ctx.pincode:
                    comp.postal_code = ctx.pincode
                    ver["postal_code"] = "conflict" if not ctx.pin_valid else "unverified"
                conf_in.level = level
                conf_in.top_score = 0.9 * c.similarity
                # Several cities/districts with this name in different states and no state given
                if len({pl.state for pl, _ in self._admin_places_for(item[2])}) > 1 and not mentioned_state:
                    conf_in.runner_up_score = conf_in.top_score
            elif mentioned_state:
                p, c, _ = mentioned_state
                level = "state"
                comp.state = p.name
                ver["state"] = "verified" if c.match_type in ("exact", "alias") else "corrected"
                conf_in.level = "state"
                conf_in.top_score = 0.9 * c.similarity
            if ctx.pincode and not ctx.pin_valid and "postal_code" not in ver:
                comp.postal_code = ctx.pincode
                ver["postal_code"] = "conflict"
            # Unmatched locality-like text when only coarse levels resolved: offer alternatives from ranked list
            for alt in ranked[: s.max_alternatives]:
                alternatives.append(self._candidate_model(alt))

        # ---- street verification against OSM streets near the locality (when imported)
        if top is not None and comp.street and top.place.lat is not None and g.stats.entities.get("street"):
            def _sk(x: str) -> str:
                return compact_key(re.sub(r"\b(road|rd|street|st|main)\b", "", x.lower()))
            want = _sk(comp.street.split(",")[0])
            for sp, _d in g.nearby(top.place.lat, top.place.lon, 3.0, types=("street",)):
                if want and _sk(sp.name) == want:
                    comp.street = sp.name if "," not in comp.street else comp.street
                    ver["street"] = "verified"
                    break

        # ---- landmark
        named = [lm for lm in ex.landmarks if lm.text]
        if named:
            lm = named[0]
            comp.landmark = lm.text
            ver["landmark"] = "unverified"
            found = self._match_landmark(top.place, ex.landmarks) if top is not None else None
            if found is not None:
                lp, lm_used, mt, sim = found
                comp.landmark = lp.name
                if mt in ("exact",) and compact_key(lm_used.text) == compact_key(lp.name):
                    ver["landmark"] = "verified"
                else:
                    ver["landmark"] = "corrected"
                    corrections.append(M.Correction(
                        original=lm_used.text, corrected=lp.name,
                        type="entity_expansion" if mt in ("exact", "alias") else "spelling_correction",
                        reason=f"known landmark within 3 km of {top.place.name} ({mt}, similarity {sim:.2f})",
                        stage="matching"))
            if lm.generic:
                warnings.append(f"landmark '{lm.text}' is generic and cannot be used to locate the address")

        conf_in.corrections_transcription = sum(1 for corr in corrections if corr.type == "transcription_fix")
        conf_in.corrections_spelling = n_spelling
        conf_in.min_spelling_similarity = min_sim
        conf_in.uncertainty_markers = len(nz.uncertainty_markers)
        conf_in.exact_names_only = exact_only
        cres = compute_confidence(conf_in, s)
        evidence.confidence_reasons = cres.reasons
        if nz.uncertainty_markers:
            warnings.append("speaker sounded unsure: " + ", ".join(dict.fromkeys(nz.uncertainty_markers)))

        # ---- missing components & clarification
        missing = []
        if not (comp.house_number or comp.unit or comp.plot or comp.building):
            missing.append("house_number")
        if not comp.locality:
            missing.append("locality")
        if not (comp.city or comp.district):
            missing.append("city")
        if not comp.state:
            missing.append("state")
        if not comp.postal_code:
            missing.append("postal_code")
        questions = self._questions(cres.status, comp, ranked, top, missing, corrections)

        needs_confirmation = (
            cres.status not in ("exact_match", "resolved")
            or any(corr.type in ("transcription_fix", "spelling_correction") for corr in corrections)
            or bool(nz.uncertainty_markers)
            or any(v == "conflict" for v in ver.values())
            or from_landmark
        )
        needs_clarification = cres.status in ("ambiguous", "unresolved") or "house_number" in missing or (
            "locality" in missing and "postal_code" in missing)

        # ---- formatting (Stage 7)
        acronyms = self._acronyms(text)
        normalized_address = self._format(comp, unparsed if level != "none" or ex.components else unparsed, acronyms)
        if level == "none" and not ex.components:
            normalized_address = None

        geo = self._geolocation(top.place if top else None, comp, ctx)
        if level == "none":
            warnings.append("no locality, city, district, state or valid PIN code was recognised")

        sources = sorted({top.place.source} if top else set()) + (["india_post"] if ctx.pin_valid else [])
        return M.Resolution(
            input_address=text,
            normalized_transcript=nz.text,
            normalized_address=normalized_address,
            components=self._restore_case(comp, acronyms),
            component_verification=ver,  # type: ignore[arg-type]
            confidence=cres.confidence,
            resolution_status=cres.status,  # type: ignore[arg-type]
            resolution_level=level,  # type: ignore[arg-type]
            needs_confirmation=needs_confirmation,
            needs_clarification=needs_clarification,
            missing_components=missing,
            clarification_questions=questions,
            corrections=corrections,
            alternatives=alternatives,
            unparsed_text=unparsed,
            warnings=list(dict.fromkeys(warnings)),
            geolocation=geo,
            evidence=evidence,
            metadata=M.Metadata(resolver_version=RESOLVER_VERSION, data_sources=sorted(set(sources)),
                                languages_detected=sorted(nz.languages)),
        )

    @staticmethod
    def _locality_suffix(tokens: list[str], top: ScoredCandidate, unparsed: list[str]) -> str | None:
        """'Andheri West', 'Lajpat Nagar 2': keep a direction / part number spoken right after the locality."""
        words = top.mention_text.split()
        n = len(words)
        for i in range(len(tokens) - n):
            if tokens[i:i + n] == words:
                nxt = tokens[i + n]
                is_suffix = nxt in ("east", "west", "north", "south") or re.fullmatch(r"\d{1,2}|i{1,3}|iv", nxt)
                if is_suffix and nxt in unparsed:
                    unparsed.remove(nxt)
                    return nxt.title() if nxt.isalpha() and nxt not in ("i", "ii", "iii", "iv") else nxt.upper()
        return None

    def _admin_places_for(self, m: Mention) -> list[tuple[Place, Candidate]]:
        return [(self.g.places[c.place_id], c) for c in m.candidates if self.g.places[c.place_id].type in ("city", "district")]

    def _runner_up(self, ranked: list[ScoredCandidate], top: ScoredCandidate) -> ScoredCandidate | None:
        for alt in ranked[1:]:
            different_place = (alt.place.state, alt.place.district) != (top.place.state, top.place.district)
            same_mention_rival = (alt.mention_text == top.mention_text and alt.place.name != top.place.name
                                  and alt.candidate.match_type == top.candidate.match_type)
            if different_place or same_mention_rival:
                return alt
        return None

    def _pin_unambiguous(self, p: Place) -> bool:
        """True if no other post office in the district has a name that overlaps with this one."""
        key = compact_key(p.name)
        if len(key) < 4:
            return False
        for lid in self.g.district_localities.get((p.state, p.district), ()):
            o = self.g.places[lid]
            if o.id == p.id or o.pincode == p.pincode:
                continue
            ok = compact_key(o.name)
            if ok.startswith(key) or key.startswith(ok):
                return False
        return True

    def _candidate_model(self, sc: ScoredCandidate) -> M.Candidate:
        p = sc.place
        city = self.g.city_for_locality(p)
        parts = [p.name, city.name if city else None, p.district if not city else None, p.state]
        addr = ", ".join(x for x in parts if x) + (f" {p.pincode}" if p.pincode else "") + ", India"
        return M.Candidate(normalized_address=addr, locality=p.name, city=city.name if city else None,
                           district=p.district, state=p.state, postal_code=p.pincode, score=round(sc.score, 3),
                           match_type=sc.candidate.match_type, matched_text=sc.mention_text)

    def _questions(self, status: str, comp: M.AddressComponents, ranked: list[ScoredCandidate],
                   top: ScoredCandidate | None, missing: list[str], corrections: list[M.Correction]
                   ) -> list[M.ClarificationQuestion]:
        qs: list[M.ClarificationQuestion] = []
        if status == "ambiguous" and top is not None:
            opts = []
            for sc in ranked[:4]:
                city = self.g.city_for_locality(sc.place)
                where = city.name if city else sc.place.district
                label = f"{sc.place.name}, {where}"
                if label not in opts:
                    opts.append(label)
            qs.append(M.ClarificationQuestion(field="locality",
                                              question=f"Which {top.place.name} do you mean: " + " or ".join(opts) + "?",
                                              options=opts))
        elif status == "unresolved":
            qs.append(M.ClarificationQuestion(field="locality",
                                              question="Could you tell me the area or locality name and the city?"))
        for corr in corrections:
            if corr.type == "spelling_correction" and corr.stage == "matching":
                qs.append(M.ClarificationQuestion(field="locality", question=f"Did you mean {corr.corrected}?",
                                                  options=[corr.corrected]))
                break
        if "house_number" in missing:
            qs.append(M.ClarificationQuestion(field="house_number",
                                              question="Could you share the house, flat or building number?"))
        if "locality" in missing and status != "unresolved":
            qs.append(M.ClarificationQuestion(field="locality", question="Which area or locality is this in?"))
        if "city" in missing and "locality" in missing:
            qs.append(M.ClarificationQuestion(field="city", question="Which city or town is this?"))
        if "postal_code" in missing and comp.locality is None:
            qs.append(M.ClarificationQuestion(field="postal_code", question="What is the 6-digit PIN code?"))
        return qs

    @staticmethod
    def _acronyms(text: str) -> dict[str, str]:
        out = {}
        for w in re.findall(r"\b[A-Z][A-Z0-9]{1,6}\b", text):
            out[w.lower()] = w
        return out

    @staticmethod
    def _restore_case(comp: M.AddressComponents, acronyms: dict[str, str]) -> M.AddressComponents:
        if not acronyms:
            return comp
        data = comp.model_dump()
        for k in UNVERIFIABLE_COMPONENTS + ("landmark",):
            v = data.get(k)
            if isinstance(v, str):
                data[k] = " ".join(acronyms.get(w.lower(), w) for w in v.split())
        return M.AddressComponents(**data)

    def _format(self, comp: M.AddressComponents, unparsed: list[str], acronyms: dict[str, str]) -> str:
        c = self._restore_case(comp, acronyms)
        parts: list[str] = []
        if c.unit:
            parts.append(c.unit if re.search(r"[a-zA-Z]", c.unit) and not c.unit[0].isdigit() else f"Flat {c.unit}")
        if c.floor:
            parts.append(c.floor)
        if c.house_number:
            parts.append(f"No. {c.house_number}" if re.match(r"^\d", c.house_number) else c.house_number)
        if c.building:
            parts.append(c.building)
        if c.plot:
            parts.append(f"Plot {c.plot}")
        for k in ("street", "cross_road", "lane", "block", "pocket", "sector", "phase", "stage"):
            v = getattr(c, k)
            if v:
                parts.append(v)
        if c.landmark:
            parts.append(f"Near {c.landmark}")
        for u in unparsed:
            if all(w in COMMON_WORDS for w in u.split()):
                continue  # chatter ("the place with") is reported in unparsed_text, not printed on a label
            parts.append(" ".join(acronyms.get(w, w) for w in smart_title(u).split()))
        if c.sub_locality:
            parts.append(c.sub_locality)
        if c.locality:
            parts.append(c.locality)
        if c.sub_district:
            parts.append(f"{c.sub_district} Tehsil")
        if c.city:
            parts.append(c.city)
        elif c.district:
            parts.append(f"{c.district} District")
        tail = c.state or ""
        if c.postal_code:
            tail = f"{tail} {c.postal_code}".strip()
        if tail:
            parts.append(tail)
        parts.append("India")
        # de-duplicate adjacent repeats (e.g. locality == city)
        out: list[str] = []
        for p in parts:
            if not out or compact_key(out[-1]) != compact_key(p):
                out.append(p)
        return ", ".join(out)

    def _geolocation(self, p: Place | None, comp: M.AddressComponents, ctx: AdminContext) -> M.Geolocation:
        g = self.g
        if p is not None and p.lat is not None:
            return M.Geolocation(latitude=round(p.lat, 6), longitude=round(p.lon, 6), accuracy="post_office_area",
                                 accuracy_note="Coordinates of the post office serving this locality "
                                               "(typically within 1-5 km of the address).", source=p.source)
        pin = comp.postal_code if comp.postal_code and comp.postal_code in g.pin_place else None
        if pin:
            pp = g.places[g.pin_place[pin]]
            if pp.lat is not None:
                return M.Geolocation(latitude=round(pp.lat, 6), longitude=round(pp.lon, 6), accuracy="pincode_centroid",
                                     accuracy_note="Median of post office coordinates in this PIN code.",
                                     source="india_post")
        if comp.district and comp.state:
            did = g.district_place.get((comp.state, comp.district))
            if did is not None and g.places[did].lat is not None:
                dp = g.places[did]
                return M.Geolocation(latitude=round(dp.lat, 6), longitude=round(dp.lon, 6), accuracy="district_centroid",
                                     accuracy_note="Approximate district centre; not suitable for delivery routing.",
                                     source="india_post")
        return M.Geolocation()
