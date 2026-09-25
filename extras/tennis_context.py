#!/usr/bin/env python3
"""Cloud-only tennis place snapshots and bounded historical weather research.

Wikidata: CC0. Open-Meteo data: CC-BY-4.0; free API use is noncommercial.
No automatic match joins, historical forecasts, training, or gambling execution.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

USER_AGENT = "SportsPropsResearch/1.0 (https://github.com/Spoofyy-1/Tabular-Model-for-sports; public noncommercial research)"
SPARQL_URL = "https://query.wikidata.org/sparql"
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY = ["temperature_2m_max", "temperature_2m_min", "temperature_2m_mean",
         "precipitation_sum", "wind_speed_10m_max", "wind_direction_10m_dominant",
         "shortwave_radiation_sum", "relative_humidity_2m_mean", "surface_pressure_mean"]
MAX_BYTES = 500_000_000
QUERY = """
SELECT DISTINCT ?entity ?entityLabel ?entityAltLabel ?place ?placeLabel
 ?relation ?latitude ?longitude ?elevation ?elevation_unit ?countryLabel ?capacity ?sitelinks WHERE {
 ?entity wdt:P641 wd:Q847 .
 FILTER NOT EXISTS { ?entity wdt:P31 wd:Q5 }
 { ?entity wdt:P276 ?place . BIND("event_location" AS ?relation) }
 UNION { ?entity wdt:P625 ?coord . BIND(?entity AS ?place) BIND("entity_coordinate" AS ?relation) }
 ?place p:P625 ?coordinateStatement .
 FILTER NOT EXISTS { ?coordinateStatement wikibase:rank wikibase:DeprecatedRank }
 ?coordinateStatement psv:P625 ?coordinateValue .
 ?coordinateValue wikibase:geoGlobe wd:Q2; wikibase:geoLatitude ?latitude; wikibase:geoLongitude ?longitude .
 OPTIONAL { ?place p:P2044/psv:P2044 ?elevationValue . ?elevationValue wikibase:quantityAmount ?elevation; wikibase:quantityUnit ?elevation_unit }
 OPTIONAL { ?place wdt:P17 ?country }
 OPTIONAL { ?place wdt:P1083 ?capacity }
 OPTIONAL { ?entity wikibase:sitelinks ?sitelinks }
 SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
} ORDER BY DESC(?sitelinks) ?entity ?place ?latitude ?longitude LIMIT 3000
"""


def now():
    return datetime.now(timezone.utc).isoformat()


class Remote:
    def __init__(self):
        self.transferred = 0
        self.manifests = []

    def get_json(self, url, params, source, limit=30_000_000, attempts=3):
        require_github_hosted_runner()
        for attempt in range(attempts):
            try:
                with requests.get(url, params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, stream=True, timeout=(20, 150)) as response:
                    if response.status_code == 429:
                        wait = min(60, max(10, int(response.headers.get("Retry-After", "30"))))
                        time.sleep(wait)
                    response.raise_for_status()
                    parts, size = [], 0
                    for block in response.iter_content(1024 * 1024):
                        size += len(block)
                        self.transferred += len(block)
                        if size > limit or self.transferred > MAX_BYTES:
                            raise ValueError("Tennis context source byte budget exceeded")
                        parts.append(block)
                    raw = b"".join(parts)
                    record = {"source": source, "url": response.url, "retrieved_at_utc": now(), "bytes": size,
                              "sha256": hashlib.sha256(raw).hexdigest(), "http_last_modified": response.headers.get("Last-Modified")}
                    self.manifests.append(record)
                    return json.loads(raw), record
            except (requests.RequestException, json.JSONDecodeError):
                if attempt == attempts - 1:
                    raise
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError("Unreachable download state")


def normalize_bindings(payload):
    """Pure transformation, permitting only small synthetic local tests."""
    bindings = payload.get("results", {}).get("bindings")
    if not isinstance(bindings, list):
        raise ValueError("Wikidata SPARQL JSON bindings missing")
    columns = ["entity", "entityLabel", "entityAltLabel", "place", "placeLabel", "relation", "latitude", "longitude", "elevation", "elevation_unit", "countryLabel", "capacity", "sitelinks"]
    frame = pd.DataFrame([{name: row.get(name, {}).get("value") for name in columns} for row in bindings], columns=columns)
    for old, new in [("entity", "entity_id"), ("place", "place_id")]:
        frame[new] = frame[old].astype("string").str.extract(r"/(Q[1-9][0-9]*)$", expand=False)
    for name in ["latitude", "longitude", "elevation", "capacity", "sitelinks"]:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame["source_geometry_valid"] = frame.entity_id.notna() & frame.place_id.notna() & frame.latitude.between(-90, 90) & frame.longitude.between(-180, 180)
    frame["historical_place_assignment_verified"] = False
    frame["record_asof_before_match_verified"] = False
    frame["automatic_training_join_allowed"] = False
    frame["source_license"] = "CC0-1.0"
    frame["availability_class"] = "current_wikidata_snapshot_no_historical_validity_claim"
    frame["place_coordinate_id"] = pd.Series(pd.NA, index=frame.index, dtype="string")
    for index in frame.index[frame.source_geometry_valid]:
        row = frame.loc[index]
        frame.loc[index, "place_coordinate_id"] = row.place_id + "-" + hashlib.sha256((str(row.latitude) + "," + str(row.longitude)).encode()).hexdigest()[:12]
    return frame.drop_duplicates().sort_values(["entity_id", "place_id", "latitude", "longitude"], na_position="last").reset_index(drop=True)


def fallback_places(remote):
    """Bounded API fallback if the public query service times out.

    Search finds tennis-tagged entities with coordinates; it does not silently
    invent tournament-to-venue links. Only non-deprecated Earth coordinates pass.
    """
    api = "https://www.wikidata.org/w/api.php"
    search, _ = remote.get_json(api, {"action": "query", "list": "search", "srsearch": "haswbstatement:P641=Q847 haswbstatement:P625", "srnamespace": 0, "srlimit": 50, "format": "json", "maxlag": 5}, "wikidata_search")
    identifiers = [row["title"] for row in search.get("query", {}).get("search", []) if re.fullmatch(r"Q[1-9][0-9]*", row.get("title", ""))]
    if not identifiers:
        raise ValueError("Wikidata fallback returned no tennis entity identities")
    data, _ = remote.get_json(api, {"action": "wbgetentities", "ids": "|".join(identifiers), "props": "labels|aliases|claims|sitelinks", "languages": "en", "format": "json", "maxlag": 5}, "wikidata_entities")
    bindings = []
    for identifier, entity in data.get("entities", {}).items():
        label = entity.get("labels", {}).get("en", {}).get("value", identifier)
        aliases = ", ".join(a["value"] for a in entity.get("aliases", {}).get("en", []))
        for claim in entity.get("claims", {}).get("P625", []):
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
            if claim.get("rank") == "deprecated" or value.get("globe", "").rsplit("/", 1)[-1] != "Q2":
                continue
            values = {"entity": "http://www.wikidata.org/entity/" + identifier, "place": "http://www.wikidata.org/entity/" + identifier,
                      "entityLabel": label, "placeLabel": label, "entityAltLabel": aliases, "latitude": value.get("latitude"), "longitude": value.get("longitude"),
                      "sitelinks": len(entity.get("sitelinks", {})), "relation": "entity_coordinate_fallback"}
            bindings.append({name: {"value": str(value)} for name, value in values.items() if value is not None})
    return {"results": {"bindings": bindings}}


def select_weather_places(places, maximum):
    valid = places.loc[places.source_geometry_valid].copy()
    # Popularity is only a bounded collection priority; no performance ranking.
    valid["_linked"] = valid.relation.eq("event_location")
    valid = valid.sort_values(["sitelinks", "_linked", "place_id", "latitude", "longitude"], ascending=[False, False, True, True, True], na_position="last")
    return valid.drop_duplicates("place_coordinate_id").head(maximum).drop(columns="_linked").reset_index(drop=True)


def weather_frame(payload, place, start, end):
    daily = payload.get("daily", {})
    if not isinstance(daily.get("time"), list) or not all(name in daily for name in DAILY):
        raise ValueError("Open-Meteo daily schema missing required fields")
    if not all(len(daily[name]) == len(daily["time"]) for name in DAILY):
        raise ValueError("Open-Meteo daily arrays have inconsistent lengths")
    frame = pd.DataFrame({name: daily[name] for name in ["time"] + DAILY}).rename(columns={"time": "weather_date_utc"})
    frame["weather_date_utc"] = pd.to_datetime(frame.weather_date_utc, utc=True, errors="coerce")
    expected = pd.date_range(start, end, freq="D", tz="UTC")
    if frame.weather_date_utc.isna().any() or frame.weather_date_utc.duplicated().any() or set(frame.weather_date_utc) != set(expected):
        raise ValueError("Weather days differ from requested complete UTC date range")
    if payload.get("utc_offset_seconds") != 0:
        raise ValueError("Weather timezone is not requested UTC")
    for name in DAILY:
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame["place_coordinate_id"] = place["place_coordinate_id"]
    frame["place_id"] = place["place_id"]
    frame["requested_latitude"] = place["latitude"]
    frame["requested_longitude"] = place["longitude"]
    frame["grid_latitude"] = payload.get("latitude")
    frame["grid_longitude"] = payload.get("longitude")
    frame["grid_elevation_m"] = payload.get("elevation")
    frame["weather_model"] = "ERA5"
    frame["source_license"] = "CC-BY-4.0"
    frame["api_access_terms"] = "Open-Meteo free API noncommercial use"
    frame["is_historical_forecast"] = False
    frame["verified_asof"] = False
    frame["automatic_training_join_allowed"] = False
    frame["historical_match_at_place_verified"] = False
    frame["availability_class"] = "postdate_reanalysis_latest_snapshot_not_pregame_forecast"
    frame["partition_by_date"] = "holdout_2025_onward"
    frame.loc[frame.weather_date_utc.lt(pd.Timestamp("2025-01-01", tz="UTC")), "partition_by_date"] = "development_through_2024"
    return frame.sort_values("weather_date_utc").reset_index(drop=True)


def write(frame, path):
    require_github_hosted_runner()
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"rows": len(frame), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "schema": {name: str(dtype) for name, dtype in frame.dtypes.items()},
            "missing_values": {name: int(count) for name, count in frame.isna().sum().items()}}


def main():
    require_github_hosted_runner()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    parser.add_argument("--weather-places", type=int, default=12)
    parser.add_argument("--skip-weather", action="store_true")
    args = parser.parse_args()
    latest = datetime.now(timezone.utc).date() - timedelta(days=7)
    if not (1940 <= args.start_year <= args.end_year <= latest.year and 1 <= args.weather_places <= 25):
        raise ValueError("Invalid bounded date/place configuration")
    start, end = date(args.start_year, 1, 1), min(date(args.end_year, 12, 31), latest)
    estimated_units = math.ceil(((end-start).days + 1) / 14) * args.weather_places * max(1, len(DAILY)/10)
    if not args.skip_weather and estimated_units > 4500:
        raise ValueError("Requested weather volume exceeds conservative 4500 weighted API-call hourly budget; reduce dates or places")
    output = ROOT / "data/extras/tennis_context"
    output.mkdir(parents=True, exist_ok=True)
    remote, tables, failures = Remote(), {}, []
    try:
        payload, _ = remote.get_json(SPARQL_URL, {"query": QUERY, "format": "json"}, "wikidata_sparql")
        retrieval = "sparql_tennis_places_and_linked_locations"
    except (requests.RequestException, ValueError) as exc:
        failures.append({"source": "wikidata_sparql", "reason": type(exc).__name__})
        payload = fallback_places(remote)
        retrieval = "bounded_mediawiki_coordinate_search_fallback"
    places = normalize_bindings(payload)
    if places.empty or not places.source_geometry_valid.any():
        raise ValueError("No valid tennis-associated coordinates collected")
    tables["wikidata_cc0/places.csv.gz"] = write(places, output / "wikidata_cc0/places.csv.gz")
    (output / "wikidata_cc0/SOURCE_QUERY.sparql").write_text(QUERY)
    (output / "wikidata_cc0/LICENSE.txt").write_text("Wikidata structured data: CC0 1.0 Universal. Source: https://www.wikidata.org/ . Terms: https://www.wikidata.org/wiki/Wikidata:Licensing . Changes: normalized entity/location coordinates and quality flags. Current snapshots do not prove historical location, altitude, capacity, or tournament assignment.\n")
    selected = select_weather_places(places, args.weather_places)
    tables["wikidata_cc0/weather_collection_places.csv.gz"] = write(selected, output / "wikidata_cc0/weather_collection_places.csv.gz")
    weather_rows, completed, units = 0, 0, {}
    if not args.skip_weather:
        weather_path = output / "weather_noncommercial_research"
        weather_path.mkdir(parents=True, exist_ok=True)
        (weather_path / "LICENSE.txt").write_text("Weather data: CC BY 4.0, Open-Meteo and Copernicus Climate Change Service/ECMWF ERA5. Changes: normalized daily UTC CSV tables, explicit coordinates, units and research flags. Free API service use is noncommercial under https://open-meteo.com/en/terms . Data license: https://creativecommons.org/licenses/by/4.0/ . Documentation and citations: https://open-meteo.com/en/docs/historical-weather-api . This bundle does not confer commercial free-API access.\n")
        for _, place in selected.iterrows():
            try:
                params = {"latitude": place.latitude, "longitude": place.longitude, "start_date": start.isoformat(), "end_date": end.isoformat(),
                          "daily": ",".join(DAILY), "models": "era5", "timezone": "GMT", "temperature_unit": "celsius", "wind_speed_unit": "kmh", "precipitation_unit": "mm"}
                response, _ = remote.get_json(WEATHER_URL, params, "open_meteo_era5", attempts=1)
                frame = weather_frame(response, place, start, end)
                units[place.place_coordinate_id] = response.get("daily_units", {})
                for year, group in frame.groupby(frame.weather_date_utc.dt.year):
                    relative = "weather_noncommercial_research/" + str(year) + "/" + place.place_coordinate_id + ".csv.gz"
                    tables[relative] = write(group, output / relative)
                weather_rows += len(frame)
                completed += 1
                print(json.dumps({"dataset": "tennis_place_weather", "places_completed": completed, "daily_rows": weather_rows}), flush=True)
            except (requests.RequestException, ValueError) as exc:
                failures.append({"source": "open_meteo_era5", "place_coordinate_id": place.place_coordinate_id, "reason": type(exc).__name__, "detail": str(exc)[:250]})
            time.sleep(1)
    summary = {"created_at_utc": now(), "dataset": "tennis_place_weather_context", "collection_status": "partial" if failures else "completed", "retrieval_method": retrieval,
               "place_relation_rows": len(places), "distinct_places": int(places.place_id.nunique()), "valid_coordinate_rows": int(places.source_geometry_valid.sum()),
               "weather_places_requested": 0 if args.skip_weather else len(selected), "weather_places_completed": completed, "weather_daily_rows": weather_rows,
               "weather_start_date": start.isoformat(), "weather_end_date": end.isoformat(), "estimated_weather_api_units_upper_bound": estimated_units,
               "source_bytes": remote.transferred, "query_row_limit": 3000, "query_limit_reached": len(payload.get("results", {}).get("bindings", [])) >= 3000,
               "sources": remote.manifests, "failures": failures, "tables": tables,
               "limitations": ["Tennis-associated Wikidata entities/locations are current snapshots, not a complete verified historical tournament venue registry.",
                   "Weather places are prioritized by Wikidata sitelinks and deduplicated coordinates; this is a bounded geographic sample, not performance-based selection.",
                   "ERA5 is postdate reanalysis with approximately 25 km grid spacing, not a court sensor or pregame forecast. Outdoor conditions do not establish indoor conditions or roof status.",
                   "Daily periods use UTC, not venue local dates or match start times. Same-day aggregates can include hours after a match.",
                   "No automatic match/weather join, travel inference, player tracking, models, rankings or profit claims. Historical venue identity/time and permitted-use review are required before model integration.",
                   "Weather free-API service use is noncommercial; the data license and API service terms are distinct."]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    (output / "schema.json").write_text(json.dumps({"null_token": r"\N", "identifiers": "Wikidata Q IDs and hashed coordinate IDs are strings", "dates": "weather_date_utc is UTC midnight starting the daily interval", "tables": tables, "weather_units": units}, indent=2))
    (output / "source_manifest.json").write_text(json.dumps({"sources": remote.manifests, "query": QUERY, "licenses": {"wikidata_cc0": "CC0-1.0", "weather_noncommercial_research": "CC-BY-4.0; free API noncommercial terms"}}, indent=2))
    print(json.dumps({key: summary[key] for key in ["collection_status", "distinct_places", "weather_daily_rows", "weather_places_completed", "source_bytes"]}), flush=True)


if __name__ == "__main__":
    main()
