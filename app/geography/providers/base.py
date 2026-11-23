"""Geographic provider abstraction.

`local` (the in-memory gazetteer) is always available. External geocoders are
opt-in (GEO_PROVIDERS=local,photon,...) AND require GEO_SEND_ADDRESS_DATA=true,
because the address text is sent to a third party. Their results are treated
as candidates and validated against the gazetteer (state / PIN consistency);
no single provider is assumed to have complete Indian coverage.

Usage terms to respect (see docs/data-sources.md):
  * Nominatim public server: max 1 request/second, identifying User-Agent,
    no bulk geocoding. Self-host Nominatim for production volumes.
  * Photon (komoot) public server: fair use only; self-host for production.
  * Geoapify: API key, free tier limits, attribution required.
  * Google Maps: NOT used (scraping or storing results violates its terms).
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx


@dataclass
class GeoResult:
    name: str
    latitude: float | None
    longitude: float | None
    provider: str
    kind: str | None = None
    state: str | None = None
    district: str | None = None
    city: str | None = None
    postcode: str | None = None
    score: float = 0.0
    raw: dict = field(default_factory=dict)


class GeoProvider(ABC):
    name = "base"
    sends_data_externally = True

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[GeoResult]:
        ...


class _RateLimiter:
    def __init__(self, min_interval_s: float) -> None:
        self.min_interval = min_interval_s
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


class NominatimProvider(GeoProvider):
    name = "nominatim"

    def __init__(self, base_url: str, user_agent: str, timeout: float = 4.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout, transport=transport, headers={"User-Agent": user_agent})
        public = "nominatim.openstreetmap.org" in self.base_url
        self.limiter = _RateLimiter(1.0 if public else 0.0)  # public usage policy: <= 1 req/s

    def search(self, query: str, limit: int = 5) -> list[GeoResult]:
        self.limiter.wait()
        r = self.client.get(f"{self.base_url}/search", params={
            "q": query, "format": "jsonv2", "addressdetails": 1, "countrycodes": "in", "limit": limit})
        r.raise_for_status()
        out = []
        for it in r.json():
            a = it.get("address", {})
            out.append(GeoResult(
                name=it.get("display_name", ""), latitude=float(it["lat"]), longitude=float(it["lon"]),
                provider=self.name, kind=it.get("type"), state=a.get("state"),
                district=a.get("state_district") or a.get("county"),
                city=a.get("city") or a.get("town") or a.get("village"), postcode=a.get("postcode"),
                score=float(it.get("importance") or 0), raw={"osm_type": it.get("osm_type"), "osm_id": it.get("osm_id")}))
        return out


class PhotonProvider(GeoProvider):
    name = "photon"

    def __init__(self, base_url: str, timeout: float = 4.0, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def search(self, query: str, limit: int = 5) -> list[GeoResult]:
        # Bias to India's centre; Photon has no country filter, so results are filtered below
        r = self.client.get(f"{self.base_url}/api", params={"q": query, "limit": limit * 2, "lat": 22.0, "lon": 79.0})
        r.raise_for_status()
        out = []
        for f in r.json().get("features", []):
            p = f.get("properties", {})
            if p.get("countrycode", "").upper() != "IN":
                continue
            lon, lat = f["geometry"]["coordinates"]
            out.append(GeoResult(
                name=p.get("name", ""), latitude=lat, longitude=lon, provider=self.name, kind=p.get("osm_value"),
                state=p.get("state"), district=p.get("county") or p.get("district"), city=p.get("city"),
                postcode=p.get("postcode"), raw={"osm_id": p.get("osm_id")}))
        return out[:limit]


class GeoapifyProvider(GeoProvider):
    name = "geoapify"

    def __init__(self, api_key: str, timeout: float = 4.0, transport: httpx.BaseTransport | None = None) -> None:
        self.api_key = api_key
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def search(self, query: str, limit: int = 5) -> list[GeoResult]:
        r = self.client.get("https://api.geoapify.com/v1/geocode/search", params={
            "text": query, "filter": "countrycode:in", "limit": limit, "format": "json", "apiKey": self.api_key})
        r.raise_for_status()
        out = []
        for it in r.json().get("results", []):
            out.append(GeoResult(
                name=it.get("formatted", ""), latitude=it.get("lat"), longitude=it.get("lon"), provider=self.name,
                kind=it.get("result_type"), state=it.get("state"), district=it.get("county") or it.get("state_district"),
                city=it.get("city"), postcode=it.get("postcode"),
                score=float((it.get("rank") or {}).get("confidence") or 0)))
        return out


def build_external_providers(settings) -> list[GeoProvider]:
    if not settings.geo_send_address_data:
        return []
    out: list[GeoProvider] = []
    for name in settings.geo_provider_list:
        if name == "nominatim":
            out.append(NominatimProvider(settings.nominatim_url, settings.nominatim_user_agent,
                                         settings.external_geocoder_timeout_seconds))
        elif name == "photon":
            out.append(PhotonProvider(settings.photon_url, settings.external_geocoder_timeout_seconds))
        elif name == "geoapify" and settings.geoapify_api_key:
            out.append(GeoapifyProvider(settings.geoapify_api_key, settings.external_geocoder_timeout_seconds))
    return out
