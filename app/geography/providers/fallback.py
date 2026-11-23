"""External geocoder fallback for finer coordinates, validated against the gazetteer.

Only used when explicitly enabled. A provider result is accepted only if it is
within `max_km` of the resolved locality (or inside the same PIN area), so a
geocoder cannot move an address into another city.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.geography.gazetteer import Gazetteer, haversine_km
from app.geography.providers.base import GeoProvider, GeoResult

log = get_logger(__name__)


class GeocoderFallback:
    def __init__(self, gazetteer: Gazetteer, providers: list[GeoProvider], max_km: float = 8.0) -> None:
        self.g = gazetteer
        self.providers = providers
        self.max_km = max_km

    def refine(self, query: str, anchor_lat: float | None, anchor_lon: float | None, pincode: str | None
               ) -> GeoResult | None:
        for prov in self.providers:
            try:
                results = prov.search(query, limit=3)
            except Exception as exc:  # network errors must not fail resolution
                log.warning("geocoder_failed", extra={"extra_fields": {"provider": prov.name,
                                                                       "error": type(exc).__name__}})
                continue
            for r in results:
                if r.latitude is None or r.longitude is None:
                    continue
                if pincode and r.postcode and r.postcode.replace(" ", "") == pincode:
                    return r
                if anchor_lat is not None and anchor_lon is not None and haversine_km(
                        anchor_lat, anchor_lon, r.latitude, r.longitude) <= self.max_km:
                    return r
        return None
