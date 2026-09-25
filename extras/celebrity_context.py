"""Bounded, cloud-only extraction of cited public spectator-presence facts.

The local catalog contains source URLs and parser choices, never seeded attendance
rows. Article bodies and images are not redistributed. No missing row means absent.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

# URLs are source configuration, not a manually constructed attendance dataset.
SOURCES = [
    {"url": "https://www.nba.com/game/mia-vs-bos-0042200305", "parser": "nba_watch_section"},
    {"url": "https://www.nba.com/game/phx-vs-cha-0021900293", "parser": "nba_watch_section"},
    {"url": "https://www.usopen.org/amp/en_US/news/articles/2023-09-10/best_photos_of_timothee_chalamet_kylie_jenner_and_other_celebrities_at_the_2023_us_open_mens_final.html", "parser": "usta_caption"},
    {"url": "https://www.usopen.org/amp/en_US/news/articles/2024-09-08/from_taylor_swift_to_simone_biles_the_best_celebrity_moments_of_the_2024_us_open.html", "parser": "usta_caption"},
    {"url": "https://www.usopen.org/amp/en_US/news/articles/2024-09-08/in_her_tennis_era_taylor_swift_and_travis_kelce_attend_2024_us_open.html", "parser": "audit_only_mixed_events"},
    {"url": "https://www.nfl.com/news/taylor-swift-takes-in-travis-kelce-chiefs-win-over-jets-at-metlife-stadium", "parser": "audit_only_mixed_events"},
    {"url": "https://www.nfl.com/news/reba-mcentire-post-malone-andra-day-announced-as-pregame-entertainment-lineup-super-bowl-lviii", "parser": "audit_only_announced_not_observed"},
]
MONTHS = {name.lower(): number for number, name in enumerate(
    ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]) if name}
MONTHS.update({name[:3]: number for name, number in list(MONTHS.items())})
MONTHS["sept"] = 9
DATE_RE = re.compile(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
FUTURE = re.compile(r"\b(will|expected|scheduled|invited|plans? to|hopes? to|could attend|may attend)\b", re.I)
RIGHTS = "No open content license identified; minimal cited factual metadata only; commercial reuse not cleared"
ATTENDANCE_COLUMNS = ["sport", "tour", "event_key", "event_date", "event_date_derivation", "event_start_utc", "official_game_id", "canonical_espn_game_id", "celebrity_name_as_reported", "presence_observed", "source_url", "source_published_at_utc", "source_published_date", "publication_precision", "publication_basis", "retrieved_at_utc", "article_sha256", "evidence_block_sha256", "extraction_rule", "named_entity_model", "identity_independently_verified", "historical_publication_time_verified", "eligible_for_pregame_feature", "automatic_training_join_allowed", "partition_by_event_date", "rights_status"]


def clean(text):
    return re.sub(r"\s+", " ", str(text)).strip().replace("’", "'")


def hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def date_in_text(text):
    match = DATE_RE.search(text)
    if not match:
        return None
    try:
        return datetime(int(match[3]), MONTHS[match[1].lower()], int(match[2])).date().isoformat()
    except ValueError:
        return None


def publication_metadata(candidates, text, url):
    """Keep explicit timezone precision; never convert a date to a fake midnight."""
    for candidate, basis in candidates:
        try:
            stamp = datetime.fromisoformat(str(candidate).replace("Z", "+00:00"))
            if "T" in str(candidate) and stamp.tzinfo:
                return {"source_published_at_utc": stamp.astimezone(timezone.utc).isoformat(),
                        "source_published_date": stamp.date().isoformat(), "publication_precision": "timestamp_with_timezone", "publication_basis": basis}
        except (ValueError, TypeError):
            continue
    # Official USTA AMP pages print this date/time including an explicit EDT/EST.
    match = re.search(r"\b(\d{1,2}) (\w+) (\d{4}) (\d{1,2}):(\d{2}) (AM|PM) (EDT|EST)\b", clean(text), re.I)
    if match and match[2].lower() in MONTHS:
        hour = int(match[4]) % 12 + (12 if match[6].upper() == "PM" else 0)
        try:
            stamp = datetime(int(match[3]), MONTHS[match[2].lower()], int(match[1]), hour, int(match[5]),
                             tzinfo=timezone(timedelta(hours=-4 if match[7].upper() == "EDT" else -5)))
            return {"source_published_at_utc": stamp.astimezone(timezone.utc).isoformat(),
                    "source_published_date": stamp.date().isoformat(), "publication_precision": "timestamp_with_timezone", "publication_basis": "visible_article_publication_label"}
        except ValueError:
            pass
    for candidate, basis in candidates:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(candidate)):
            return {"source_published_at_utc": None, "source_published_date": str(candidate), "publication_precision": "date_only", "publication_basis": basis}
    match = re.search(r"/articles/(\d{4}-\d{2}-\d{2})/", url)
    return {"source_published_at_utc": None, "source_published_date": match[1] if match else None,
            "publication_precision": "url_date_only" if match else "unknown", "publication_basis": "source_url_path" if match else None}


def json_nodes(value):
    if isinstance(value, dict):
        yield value
        for subvalue in value.values():
            yield from json_nodes(subvalue)
    elif isinstance(value, list):
        for subvalue in value:
            yield from json_nodes(subvalue)


def extract_document(html, url):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.find("meta", property="og:title")
    title = clean(title_node.get("content", "")) if title_node else clean(soup.title.get_text()) if soup.title else ""
    candidates = []
    for node in soup.find_all("meta"):
        if node.get("property", node.get("name", "")).lower() in {"article:published_time", "datepublished", "pubdate", "publishdate"}:
            candidates.append((node.get("content"), "article_meta_publication"))
    for node in soup.find_all("script", type="application/ld+json"):
        try:
            for item in json_nodes(json.loads(node.get_text())):
                types = item.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if any(t in {"Article", "NewsArticle", "ReportageNewsArticle"} for t in types) and item.get("datePublished"):
                    candidates.append((item["datePublished"], "article_jsonld_datePublished"))
        except (ValueError, TypeError):
            pass
    for node in soup(["script", "style", "nav", "footer"]):
        node.decompose()
    blocks = [clean(node.get_text(" ", strip=True)) for node in soup.find_all(["p", "h1", "h2", "h3", "h4", "figcaption"])]
    # Captions may live only in alt attributes. We never request image bytes.
    blocks += [clean(node.get("alt", "")) for node in soup.find_all(["img", "amp-img"])]
    blocks = list(dict.fromkeys(block for block in blocks if block))
    full_text = clean(soup.get_text(" ", strip=True))
    return {"title": title, "blocks": blocks, "text": full_text,
            "publication": publication_metadata(candidates, full_text, url)}


def nba_watch_blocks(blocks):
    found = []
    active = False
    for block in blocks:
        text = clean(block)
        if text in {"VIP WATCH", "CELEBRITY WATCH", "CELEBRITY SIGHTINGS"}:
            active = True
            continue
        if active and (len(text) < 90 and text.upper() == text or text.startswith("More AP NBA")):
            active = False
        if active and len(text) > 15 and not FUTURE.search(text):
            # Only scoped section paragraphs with a concrete presence predicate.
            if re.search(r"\b(on hand|celebrities in|in attendance|in the (?:garden|arena|stands)|courtside|at the game|so was)\b", text, re.I):
                found.append(text)
    return found


def usta_caption(text, document):
    """Only finals identify one match uniquely without a canonical match crosswalk."""
    text = clean(text)
    if len(text) > 900 or FUTURE.search(text):
        return None
    match = re.search(r"\b(?:in attendance )?(?:during|attends) a (men|women)'s singles championship match at the (\d{4}) US Open", text, re.I)
    if not match:
        match = re.search(r"\battend (?:the )?(men|women)'s final match between .+?US Open", text, re.I)
    if not match:
        return None
    prefix = text[:match.start()]
    gender = match[1].lower()
    event_date = date_in_text(text[match.start():])
    basis = "explicit_caption_calendar_date"
    if not event_date:
        title = document["title"].lower().replace("’", "'")
        published = document["publication"]["source_published_date"]
        expected_gender = "men" if gender == "men" else "women"
        # Dedicated final-day gallery, with an explicit same-weekday statement.
        weekday_match = re.search(r"(?:stadium|stands) (?:on )?(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\b", document["text"], re.I)
        if published and re.search(r"\b" + expected_gender + r"'?s final\b", title) and weekday_match:
            if datetime.fromisoformat(published).strftime("%A").lower() == weekday_match[1].lower():
                event_date, basis = published, "same_day_final_gallery_weekday_and_publication"
    if not event_date:
        return None
    # A caption mentioning a tournament year must agree with its actual event date.
    tournament_year = re.search(r"\b(\d{4}) US Open", text)
    if tournament_year and not event_date.startswith(tournament_year[1]):
        return None
    return {"prefix": prefix, "event_date": event_date, "event_date_derivation": basis,
            "sport": "tennis", "tour": "ATP" if gender == "men" else "WTA",
            "event_key": "usopen:" + event_date + ":" + gender + ":singles_final",
            "event_start_utc": None, "official_game_id": None, "canonical_espn_game_id": None}


def normalize_people(nlp, text):
    people = []
    for entity in nlp(text).ents:
        name = clean(entity.text).strip(" ,.;:()")
        # Conservative: omit single-token/partial entities and retain spelling.
        if entity.label_ == "PERSON" and 2 <= len(name.split()) <= 6 and not any(char.isdigit() for char in name):
            people.append(name)
    return list(dict.fromkeys(people))


def attendance_row(event, name, block, source, document, retrieved, rule):
    row = {key: value for key, value in event.items() if key != "prefix"}
    row.update(document["publication"])
    row.update({"celebrity_name_as_reported": name, "presence_observed": True, "source_url": source["url"],
                "retrieved_at_utc": retrieved, "article_sha256": source["sha256"],
                "evidence_block_sha256": hash_text(block), "extraction_rule": rule,
                "named_entity_model": "en_core_web_sm:3.8.0", "identity_independently_verified": False,
                "historical_publication_time_verified": False, "eligible_for_pregame_feature": False,
                "automatic_training_join_allowed": False, "rights_status": RIGHTS,
                "partition_by_event_date": "development_through_2024" if event["event_date"] < "2025-01-01" else "holdout_2025_onward"})
    return row


def fetch(url, max_bytes=8_000_000):
    require_github_hosted_runner()
    if urlparse(url).hostname not in {"www.nba.com", "cdn.nba.com", "www.usopen.org", "www.nfl.com"}:
        raise ValueError("Source host is outside documented catalog")
    with requests.get(url, stream=True, timeout=(15, 45), headers={"User-Agent": "sports-context-research/1.0"}) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(256 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("Source exceeded bounded byte limit")
            chunks.append(chunk)
        return b"".join(chunks)


def nba_event(source_url, document):
    match = re.search(r"\b(00\d{8})\b", source_url)
    if not match:
        raise ValueError("NBA URL missing exact game identity")
    game_id = match[1]
    event = {"sport": "NBA", "tour": None, "event_key": "nba:" + game_id, "official_game_id": game_id,
             "canonical_espn_game_id": None, "event_date": date_in_text(document["title"]),
             "event_date_derivation": "official_game_page_title", "event_start_utc": None}
    player_names, auxiliary = [], {"status": "not_available"}
    url = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_" + game_id + ".json"
    try:
        raw = fetch(url, 2_000_000)
        game = json.loads(raw)["game"]
        if str(game["gameId"]) != game_id:
            raise ValueError("Official boxscore identity mismatch")
        stamp = datetime.fromisoformat(game["gameTimeUTC"].replace("Z", "+00:00"))
        if not stamp.tzinfo:
            raise ValueError("Official game time missing timezone")
        event["event_start_utc"] = stamp.astimezone(timezone.utc).isoformat()
        # Official page title reports the local calendar date; retain that date.
        if not event["event_date"]:
            event["event_date"] = stamp.astimezone(timezone.utc).date().isoformat()
            event["event_date_derivation"] = "official_game_start_utc_date"
        for side in ("homeTeam", "awayTeam"):
            player_names += [clean(p["name"]) for p in game.get(side, {}).get("players", []) if p.get("name")]
        auxiliary = {"url": url, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "status": "ok"}
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        auxiliary = {"url": url, "status": "unavailable", "error_class": type(exc).__name__}
    if not event["event_date"]:
        raise ValueError("NBA event date could not be established")
    return event, player_names, auxiliary


def write(frame, path):
    require_github_hosted_runner()
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"file": path.name, "rows": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "columns": {name: str(dtype) for name, dtype in frame.dtypes.items()},
            "missing": {name: int(value) for name, value in frame.isna().sum().items()}}


def main():
    require_github_hosted_runner()
    import spacy
    nlp = spacy.load("en_core_web_sm", disable=["parser", "lemmatizer"])
    if nlp.meta.get("version") != "3.8.0":
        raise RuntimeError("Expected pinned en_core_web_sm version 3.8.0")
    output = ROOT / "data/extras/celebrity_context"
    output.mkdir(parents=True, exist_ok=True)
    retrieved = datetime.now(timezone.utc).isoformat()
    rows, inventory, auxiliaries = [], [], []
    for item in SOURCES:
        source = dict(item)
        source.update({"retrieved_at_utc": retrieved, "rights_status": RIGHTS, "rows_extracted": 0})
        stage = "fetch_article"
        try:
            raw = fetch(item["url"])
            source.update({"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            stage = "parse_article"
            document = extract_document(raw.decode("utf-8", errors="replace"), item["url"])
            source.update(document["publication"])
            source["blocks_scanned"] = len(document["blocks"])
            start_rows = len(rows)
            if item["parser"] == "nba_watch_section":
                stage = "establish_nba_event"
                event, players, auxiliary = nba_event(item["url"], document)
                auxiliaries.append(auxiliary)
                for block in nba_watch_blocks(document["blocks"]):
                    for name in normalize_people(nlp, block):
                        if name not in players:
                            rows.append(attendance_row(event, name, block, source, document, retrieved, "nba_scoped_watch_section"))
            elif item["parser"] == "usta_caption":
                stage = "extract_usta_caption"
                for block in document["blocks"]:
                    event = usta_caption(block, document)
                    if event:
                        # Only names BEFORE the match description, never opponents/photographers.
                        for name in normalize_people(nlp, event["prefix"]):
                            rows.append(attendance_row(event, name, block, source, document, retrieved, "usta_final_spectator_caption"))
            source["rows_extracted"] = len(rows) - start_rows
            source["status"] = "audit_only" if item["parser"].startswith("audit_only") else "parsed" if source["rows_extracted"] else "no_qualified_evidence"
            del raw, document
        except Exception as exc:
            # Error metadata only: no article text, response bodies, or celebrity rows in logs.
            source.update({"status": "error", "error_stage": stage, "error_class": type(exc).__name__})
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                source["http_status"] = exc.response.status_code
        inventory.append(source)
        print(json.dumps({"source_number": len(inventory), "status": source["status"], "rows_extracted": source["rows_extracted"]}), flush=True)
        time.sleep(0.5)
    frame = pd.DataFrame(rows, columns=ATTENDANCE_COLUMNS)
    if len(frame):
        frame = frame.drop_duplicates(["event_key", "celebrity_name_as_reported", "source_url"]).sort_values(["sport", "event_date", "event_key", "celebrity_name_as_reported", "source_url"])
    table = write(frame, output / "documented_presence.csv.gz")
    inventory_table = write(pd.DataFrame(inventory).sort_values("url"), output / "source_catalog.csv.gz")
    summary = {"retrieved_at_utc": retrieved, "sources_attempted": len(inventory), "source_bytes": sum(x.get("bytes", 0) for x in inventory),
               "source_statuses": dict(pd.Series([x["status"] for x in inventory]).value_counts().items()),
               "documented_presence_rows": len(frame), "unique_events": int(frame.event_key.nunique()),
               "rows_by_sport": frame.sport.value_counts().to_dict(), "rows_with_exact_publication_timestamp": int(frame.source_published_at_utc.notna().sum()),
               "earliest_event_date": frame.event_date.min() if len(frame) else None, "latest_event_date": frame.event_date.max() if len(frame) else None,
               "tables": {"documented_presence": table, "source_catalog": inventory_table}, "sources": inventory, "auxiliary_sources": auxiliaries,
               "limitations": ["Tiny, selected press sample; never infer nonattendance or absence from a missing row.",
                   "No open bulk celebrity-attendance license found. Original article prose and images are never redistributed.",
                   "Derived names are machine-extracted and not independently identity-verified. Single-name celebrities and uncertain captions are omitted.",
                   "Only exact NBA games or uniquely identified US Open singles finals qualify; multi-match sessions and announced appearances are excluded.",
                   "Source publication metadata comes from today's retrieved article, not an immutable historical capture.",
                   "All pregame-feature and automatic-training permissions remain false; most evidence is retrospective.",
                   "No evidence of a causal performance effect or betting profitability is established."]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=int))
    (output / "schema.json").write_text(json.dumps({"csv_null_encoding": r"\N", "empty_text_distinct_from_null": True,
        "tables": summary["tables"], "event_key": "NBA official game ID; or tournament/calendar date/gender/singles final, not a canonical player match ID",
        "presence_observed": "Positive documented observation only; no zero/negative examples", "rights_status": RIGHTS}, indent=2))
    (output / "SOURCE_RIGHTS.md").write_text("# Source rights and attribution\n\n" + RIGHTS + ".\n\nNBA.com/AP and USTA/USOpen.org retain rights in their articles and images. Only a bounded set of factual presence associations and source metadata is exported. This does not grant an unrestricted content or commercial-use license. No source body or image is included.\n")


if __name__ == "__main__":
    main()
