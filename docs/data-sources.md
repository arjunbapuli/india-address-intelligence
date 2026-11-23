# Geographic data: sources, licensing, ingestion

## What is loaded by default

| Entity type | Count | Source |
|---|---|---|
| States and UTs | 36 | India Post (names mapped to official spellings by curated data) |
| Districts | 754 | India Post |
| PIN codes | 19,586 | India Post |
| Localities (post-office areas, towns, villages) | 165,515 | India Post office names, cleaned |
| Cities | 147 | Curated (`datasets/curated/cities.json`) |
| Aliases | ~3,000 | Derived (acronyms, original India Post spellings, district suffixes) + curated |

Check what a running instance has with `GET /v1/coverage`.

### India Post: All-India Pincode Directory

- **Publisher:** Department of Posts, Government of India, on the Open Government Data Platform: https://www.data.gov.in/catalog/all-india-pincode-directory
- **Licence:** Government Open Data License - India (GODL-India). Free use, including commercial, with attribution: *"Contains data from the Department of Posts, Government of India, published on data.gov.in."*
- **How we get it:** the `indiapins` PyPI package (MIT licence) ships the directory (updated 21 February 2026). It is pinned as `indiapins==1.1.0` so ingestion is reproducible and works offline. To use a fresh download from data.gov.in instead: `python -m scripts.ingest --csv path/to/pincode.csv`.
- **Fields used:** office name, PIN, office type (HO/SO/BO), delivery status, district, state, circle, division, latitude, longitude.
- **Cleaning:** office suffixes removed ("Koramangala VI Bk S.O" → "Koramangala 6th Block"), Roman numerals and abbreviations expanded, trailing district/city names stripped ("Lajpat Nagar South Delhi" → "Lajpat Nagar", with the long form kept as an alias), acronym aliases derived ("HSR Layout" → "HSR"). Missing state/district (~700 rows) are inferred from other offices with the same PIN. Coordinates outside India, or more than 150 km from their district's median, are dropped (5,854 offices).
- **Limitations:** locality = post-office name. Many neighbourhoods are not post offices. Post-office coordinates are approximate and occasionally wrong.

### Curated naming data (this repository, MIT)

`datasets/curated/`:

- `states.json`: official names, ISO codes, abbreviations (UP, MP, TN...), old names (Orissa, Uttaranchal), names in Indian scripts.
- `districts.json`: display names and renamed/alternate names (Gurgaon → Gurugram, Allahabad → Prayagraj, Belgaum → Belagavi, 24 Paraganas North → North 24 Parganas...).
- `cities.json`: 147 cities with old/alternate names (Bombay, Madras, Calcutta, Bangalore, Poona...), the districts that make up each city, and `infer_districts`: fully urban districts where the city can be filled in automatically.
- `locality_aliases.json`: colloquial names whose India Post name differs (Salt Lake → Bidhan Nagar, T Nagar → Thygarayanagar, Ooty → Udagamandalam). Only clear naming variants are listed, never guesses about which post office serves a neighbourhood.

Tests check that every curated reference points to real India Post entities.

### OpenStreetMap (optional import)

- **Licence:** ODbL 1.0, attribution *"© OpenStreetMap contributors"*. If you redistribute a database containing OSM data, share-alike applies to that database.
- **Download:** https://download.geofabrik.de/asia/india.html. Whole India (~1.5 GB .pbf) or zones (southern, western, northern, eastern, north-eastern, central).
- **Import:** `pip install ".[osm]"` then `python -m scripts.ingest --osm southern-zone-latest.osm.pbf`. In Docker, mount the file and set `OSM_IMPORT_PATH`.
- **Imported:** `place=suburb|neighbourhood|quarter|locality|village|town|city|hamlet` → `osm_place`; named amenities, tourism, malls, stations, parks, monuments → `landmark`; named roads → `street`. Alternate names (`name:en`, `alt_name`, `old_name`, `name:hi`, `name:kn`, ...) become aliases; institution names get acronym aliases ("National Institute of Fashion Technology" → "NIFT").
- **Hierarchy:** each OSM feature gets district/state/PIN from `addr:postcode` when valid, else from the nearest India Post office. PINs are never inferred for OSM-only localities in responses.
- **Not done in this sandbox:** the development environment could not reach Geofabrik, so OSM was only tested on a small synthetic fixture (`tests/fixtures/mini.osm`). Memory grows with OSM data; budget about 1 GB RAM for a zone extract.

### External geocoders (optional, off by default)

| Provider | Terms to respect | Config |
|---|---|---|
| Nominatim (public) | ≤ 1 request/s, identifying User-Agent, no bulk geocoding. Self-host for production. | `GEO_PROVIDERS=nominatim`, `NOMINATIM_URL`, `NOMINATIM_USER_AGENT` |
| Photon (komoot public) | Fair use; self-host for volume | `GEO_PROVIDERS=photon`, `PHOTON_URL` |
| Geoapify | API key, free tier limits, attribution | `GEO_PROVIDERS=geoapify`, `GEOAPIFY_API_KEY` |
| Google Maps | **Not used.** Scraping or caching Google results violates their terms. | — |

External results only refine coordinates and are accepted only within 8 km of the resolved locality, or when the PIN matches. Sending addresses to these services also requires `GEO_SEND_ADDRESS_DATA=true`.

## Ingestion pipeline

```bash
python -m scripts.ingest                       # India Post (bundled) + curated, replaces previous rows of these sources
python -m scripts.ingest --if-empty            # only when the DB has no entities (used by the container entrypoint)
python -m scripts.ingest --csv data.csv        # India Post CSV from data.gov.in
python -m scripts.ingest --osm zone.osm.pbf    # add OpenStreetMap
```

Each run records an `ingestion_runs` row and updates `data_sources` (licence, attribution, version, record count, retrieved_at). Re-running a source replaces only that source's rows. The API loads whatever is in the database at startup, so adding data never needs code changes. Restart the API to pick up new data.

## Data sources worth adding next

| Source | Adds | Licence note |
|---|---|---|
| OSM India (Geofabrik) | Neighbourhoods, streets, landmarks | ODbL |
| LGD (Local Government Directory, lgdirectory.gov.in) | Official village/town codes, sub-districts | Government data; check terms |
| Census 2011 village/town directory | Villages with census codes and population (useful prior) | GODL-India |
| India Post PIN boundary polygons (data.gov.in, where published) | Point-in-polygon PIN validation | GODL-India |
| Reviewed customer feedback | Colloquial names actually used | Your data |
