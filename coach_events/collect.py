"""Hosted-only extraction of coaching announcement events, not full tenures."""
import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

MAX_REQUESTS, MAX_BYTES, MAX_PAGE_BYTES, MAX_REDIRECTS = 20, 20_000_000, 4_000_000, 2
RIGHTS = "No open article-content license identified; minimal cited factual annotations only; commercial and bulk-content reuse not cleared"
NAME = r"[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+(?: [A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’.-]+){1,3}"
SOURCES = [
    {"id": "nba_nets", "sport": "NBA", "team": "Brooklyn Nets", "kind": "team_appointment_release", "event_type": "appointment_announced",
     "url": "https://www.nba.com/nets/news/brooklyn-nets-name-jordi-fernandez-head-coach",
     "pattern": r"The Brooklyn Nets have named (?P<coach>" + NAME + r") as the \d+(?:st|nd|rd|th) head coach"},
    {"id": "nba_raptors", "sport": "NBA", "team": "Toronto Raptors", "kind": "team_appointment_release", "event_type": "appointment_announced",
     "url": "https://www.nba.com/raptors/news/raptors-name-darko-rajakovic-as-head-coach",
     "pattern": r"The Toronto Raptors announced (?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) they have named (?P<coach>" + NAME + r")(?: \([^)]{1,80}\))? as head coach"},
    {"id": "nba_sixers", "sport": "NBA", "team": "Philadelphia 76ers", "kind": "team_appointment_biography", "event_type": "appointment_announced",
     "url": "https://www.nba.com/sixers/news/nick-nurse-bio",
     "pattern": r"NBA Champion (?P<coach>" + NAME + r") has officially been named head coach of the Philadelphia 76ers"},
    {"id": "nfl_seahawks", "sport": "NFL", "team": "Seattle Seahawks", "kind": "team_appointment_release", "event_type": "appointment_announced",
     "url": "https://www.seahawks.com/news/mike-macdonald-named-head-coach-of-the-seattle-seahawks",
     "pattern": r"The Seahawks have hired (?P<coach>" + NAME + r") as the ninth head coach"},
    {"id": "nfl_falcons", "sport": "NFL", "team": "Atlanta Falcons", "kind": "team_appointment_release", "event_type": "appointment_announced",
     "url": "https://www.atlantafalcons.com/news/raheem-morris-hired-head-coach-announcement-arthur-blank",
     "pattern": r"The Atlanta Falcons hired (?P<coach>" + NAME + r") as their next head coach, the organization announced (?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"},
    {"id": "nfl_chargers", "sport": "NFL", "team": "Los Angeles Chargers", "kind": "team_agreement_release", "event_type": "agreement_to_terms_announced",
     "url": "https://www.chargers.com/news/chargers-name-jim-harbaugh-michigan-head-coach",
     "pattern": r"The Los Angeles Chargers today agreed to terms with (?P<coach>" + NAME + r") as head coach"},
    {"id": "nfl_panthers", "sport": "NFL", "team": "Carolina Panthers", "kind": "team_agreement_release", "event_type": "agreement_to_terms_announced",
     "url": "https://www.panthers.com/news/panthers-agree-to-terms-with-dave-canales-to-become-head-coach",
     "required_scope": r"\bPanthers\b",
     "pattern": r"The team agreed to terms with Buccaneers offensive coordinator (?P<coach>" + NAME + r") as the seventh head coach in franchise history (?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"},
    {"id": "nfl_texans", "sport": "NFL", "team": "Houston Texans", "kind": "team_appointment_release", "event_type": "appointment_announced",
     "url": "https://www.houstontexans.com/news/houston-texans-hire-demeco-ryans-as-head-coach",
     "pattern": r"The Houston Texans have hired (?P<coach>" + NAME + r") as the team's sixth head coach"},
]
COLUMNS = ["event_id", "sport", "team_name_as_reported", "coach_name_as_reported", "canonical_team_id", "canonical_coach_id", "role", "is_interim",
           "event_type", "event_date", "event_date_precision", "event_date_basis", "effective_start_date", "effective_end_date", "first_game_coached_id",
           "source_id", "source_url", "retrieved_url", "source_kind", "source_published_date", "source_published_at_utc", "publication_precision",
           "publication_time_conflict", "source_modified_at_utc", "retrieved_at_utc", "source_sha256", "source_hash_basis", "evidence_sha256", "evidence_hash_basis", "date_conflict_status",
           "historical_availability_verified", "automatic_training_join_allowed", "rights_status"]
MONTHS = {name.lower(): i for i, name in enumerate(["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]) if name}
MONTHS.update({name[:3]: number for name, number in list(MONTHS.items())})
DATE = re.compile(r"\b(" + "|".join(MONTHS) + r")\.?\s+(\d{1,2}),?\s+(\d{4})\b", re.I)


def clean(text):
    return re.sub(r"\s+", " ", str(text)).strip().replace("’", "'")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_date(value):
    text = clean(value)
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return stamp.date().isoformat(), stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else None
    except ValueError:
        pass
    match = DATE.search(text)
    if not match:
        return None, None
    try:
        stamp = datetime(int(match[3]), MONTHS[match[1].lower()], int(match[2]))
        clock = re.search(r"\b(\d{1,2}):(\d{2})\s*(AM|PM)\s+(EDT|EST|UTC|GMT)\b", text, re.I)
        if clock:
            if not 1 <= int(clock[1]) <= 12:
                return None, None
            hour = int(clock[1]) % 12 + (12 if clock[3].upper() == "PM" else 0)
            offset = {"EDT": -4, "EST": -5, "UTC": 0, "GMT": 0}[clock[4].upper()]
            stamp = stamp.replace(hour=hour, minute=int(clock[2]), tzinfo=timezone(timedelta(hours=offset)))
        return stamp.date().isoformat(), stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else None
    except ValueError:
        return None, None


def nodes(value):
    if isinstance(value, dict):
        yield value
        for key, child in value.items():
            if key.lower() not in {"related", "relatedarticles", "recommendations", "relatedstories"}:
                yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def same_page(candidate, url):
    if not isinstance(candidate, str):
        return False
    left, right = urlparse(urljoin(url, candidate)), urlparse(url)
    return left.hostname == right.hostname and left.path.rstrip("/") == right.path.rstrip("/")


def secondary(element):
    for node in [element] + list(element.parents):
        if getattr(node, "name", None) in {"aside", "nav", "footer"}:
            return True
        if hasattr(node, "get"):
            labels = " ".join(node.get("class", [])) + " " + str(node.get("id", ""))
            if re.search(r"related|recommend|(?:^|[-_ ])card(?:$|[-_ ])|teaser|promo", labels, re.I):
                return True
    return False


def document(html, url):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    publication, modified, bodies = [], [], []
    for meta in soup.find_all("meta"):
        if secondary(meta) or len(meta.find_parents("article")) > 1:
            continue
        key = str(meta.get("property") or meta.get("name") or "").lower()
        if key in {"article:published_time", "datepublished", "pubdate"}:
            publication.append((meta.get("content", ""), "meta_publication"))
        elif key in {"article:modified_time", "datemodified"}:
            modified.append(meta.get("content", ""))
    for script in soup.find_all("script"):
        if secondary(script):
            continue
        if script.get("type") != "application/ld+json" and script.get("id") != "__NEXT_DATA__":
            continue
        try:
            for item in nodes(json.loads(script.get_text())):
                linked = item.get("url") or item.get("@id") or item.get("mainEntityOfPage")
                if isinstance(linked, dict):
                    linked = linked.get("@id")
                types = item.get("@type", [])
                types = [types] if isinstance(types, str) else types
                scoped = same_page(linked, url) or (not linked and item.get("slug") == urlparse(url).path.rstrip("/").split("/")[-1])
                # Even a NewsArticle type is insufficient without page identity;
                # recommendation payloads can carry unrelated complete articles.
                if not scoped:
                    continue
                if item.get("datePublished"):
                    publication.append((item["datePublished"], "structured_publication"))
                if item.get("dateModified"):
                    modified.append(item["dateModified"])
                for key in ("articleBody", "body", "content", "contentHtml"):
                    body = item.get(key)
                    if isinstance(body, dict):
                        body = body.get("rendered")
                    if isinstance(body, str) and len(body) <= 500_000:
                        bodies.append(body)
        except (ValueError, TypeError, RecursionError):
            continue
    mains = soup.find_all("main")
    scope = mains[0] if len(mains) == 1 else soup
    articles = [item for item in scope.find_all("article") if not secondary(item) and not item.find_parent("article")]
    linked_articles = [item for item in articles if any(same_page(item.get(key), url) for key in ("itemid", "data-url"))]
    primary_articles = linked_articles if len(linked_articles) == 1 else articles if len(articles) == 1 else []
    containers = list(primary_articles)
    if not articles:
        containers = [item for item in scope.select("[itemprop='articleBody'], .nfl-c-body-part, .article-content, .article-body, [class*='Article_article'], [class*='ArticleContent']")
                      if not secondary(item) and not item.find_parent("article")]
    for item in containers:
        for junk in item.select("nav, footer, aside, script, article, [class*='related'], [class*='Related']"):
            if junk.parent is not None:
                junk.decompose()
        bodies.append(str(item))
    blocks = []
    for body in dict.fromkeys(bodies):
        fragment = BeautifulSoup(body, "html.parser")
        roots = [node for node in fragment.children if getattr(node, "name", None)]
        primary_wrapper = roots[0] if len(roots) == 1 and roots[0].name == "article" else None
        # Sanitize each fragment too: structured articleBody HTML can contain
        # recommendation widgets, and a parent's get_text would otherwise pull
        # their descendants into otherwise valid primary paragraphs.
        for node in list(fragment.find_all()):
            if node.parent is not None and (secondary(node) or (node.name == "article" and node is not primary_wrapper)):
                node.decompose()
        parts = fragment.find_all(["p", "li"])
        if parts:
            blocks.extend(clean(part.get_text(" ", strip=True)) for part in parts if not secondary(part))
        else:
            blocks.extend(clean(part) for part in fragment.get_text("\n", strip=True).splitlines())
    # Dedicated publication labels only: never mine career dates from prose.
    labels = [item for item in scope.select(".nfl-c-article__date, .article-date, [class*='ArticleDate'], [class*='ContentHeader_date']")
              if not secondary(item) and (not item.find_parent("article") or any(item.find_parent("article") is article for article in primary_articles))]
    for container in containers:
        labels.extend(item for item in container.select("time[datetime]") if not secondary(item))
    for item in labels:
        if item.get("itemprop") == "dateModified":
            modified.append(item.get("datetime") or item.get_text(" ", strip=True))
        else:
            publication.append((item.get("datetime") or item.get_text(" ", strip=True), "visible_publication_label"))
    candidates = []
    for value, basis in publication:
        day, stamp = parse_date(value)
        if day:
            candidates.append({"date": day, "timestamp_utc": stamp, "basis": basis})
    dates = set(item["date"] for item in candidates)
    stamps = set(item["timestamp_utc"] for item in candidates if item["timestamp_utc"])
    day = next(iter(dates)) if len(dates) == 1 else None
    stamp = next(iter(stamps)) if len(stamps) == 1 and day else None
    modified_stamps = {parse_date(item)[1] for item in modified} - {None}
    return {"blocks": list(dict.fromkeys(filter(None, blocks))),
            "headlines": [clean(item.get_text(" ", strip=True)) for item in scope.find_all("h1") if not secondary(item)],
            "source_published_date": day, "source_published_at_utc": stamp,
            "publication_precision": "timestamp_with_timezone" if stamp else "date_only" if day else "conflicting_dates" if dates else "unknown",
            "publication_time_conflict": len(stamps) > 1, "publication_date_conflict": len(dates) > 1,
            "publication_candidates": candidates,
            "source_modified_at_utc": next(iter(modified_stamps)) if len(modified_stamps) == 1 else None}


def extract(doc, source, html, retrieved_at, retrieved_url=None):
    if retrieved_url and not same_page(retrieved_url, source["url"]):
        return [], "retrieved_page_path_mismatch"
    # Restrict announcement matches to article lead paragraphs. Historical
    # careers and related items later on a page cannot create new appointments.
    lead = doc["blocks"][:8]
    if source.get("required_scope") and not re.search(source["required_scope"], " ".join(doc["headlines"] + lead)):
        return [], "team_scope_missing"
    matches = [(match, block) for block in lead for match in re.finditer(source["pattern"], block)]
    identities = {clean(match["coach"]).rstrip(".") for match, _ in matches}
    if len(identities) != 1:
        return [], "ambiguous_appointment_identity" if identities else "no_qualified_appointment_evidence"
    coach = next(iter(identities))
    weekdays = {match.groupdict().get("weekday") for match, _ in matches} - {None}
    day = doc["source_published_date"]
    conflict = "publication_date_conflict" if doc["publication_date_conflict"] else "none"
    if day and weekdays and (len(weekdays) != 1 or datetime.fromisoformat(day).strftime("%A") not in weekdays):
        conflict, day = "announcement_weekday_publication_mismatch", None
    row = {key: None for key in COLUMNS}
    row.update(sport=source["sport"], team_name_as_reported=source["team"], coach_name_as_reported=coach, role="head_coach", is_interim=False,
               event_type=source["event_type"], event_date=day, event_date_precision="date_only" if day else "unknown",
               event_date_basis="dated_official_announcement_not_effective_tenure_start" if day else None,
               source_id=source["id"], source_url=source["url"], retrieved_url=retrieved_url or source["url"], source_kind=source["kind"],
               retrieved_at_utc=retrieved_at, source_sha256=sha(html), source_hash_basis="decoded_text_utf8",
               evidence_sha256=sha(matches[0][1]), evidence_hash_basis="normalized_article_block_utf8",
               date_conflict_status=conflict, historical_availability_verified=False, automatic_training_join_allowed=False, rights_status=RIGHTS)
    for key in ("source_published_date", "source_published_at_utc", "publication_precision", "publication_time_conflict", "source_modified_at_utc"):
        row[key] = doc[key]
    row["event_id"] = sha(json.dumps([source["id"], coach, source["event_type"], day], ensure_ascii=False))
    return [row], "parsed_with_date_conflict" if conflict != "none" else "parsed" if day else "parsed_without_publication_date"


class Fetcher:
    def __init__(self, session):
        self.session, self.requests, self.bytes = session, 0, 0
        self.last = {}

    def get(self, url):
        require_github_hosted_runner()
        original, current, visited = urlparse(url), url, set()
        self.last = {"http_status": None, "redirects": []}
        for hop in range(MAX_REDIRECTS + 1):
            if self.requests >= MAX_REQUESTS or self.bytes >= MAX_BYTES:
                raise RuntimeError("collection_budget_exhausted")
            if current in visited:
                raise RuntimeError("redirect_loop")
            visited.add(current)
            self.requests += 1
            with self.session.get(current, stream=True, timeout=(10, 35), allow_redirects=False) as response:
                self.last.update(http_status=response.status_code, retrieved_url=current)
                if response.status_code in {301, 302, 303, 307, 308}:
                    if hop >= MAX_REDIRECTS:
                        raise RuntimeError("redirect_limit")
                    location = response.headers.get("Location")
                    destination = urljoin(current, location) if location else ""
                    parsed = urlparse(destination)
                    if parsed.scheme != "https" or parsed.hostname != original.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
                        raise RuntimeError("redirect_outside_allowed_origin")
                    if parsed.path.rstrip("/") != original.path.rstrip("/") or parsed.query != original.query:
                        raise RuntimeError("redirect_changes_article_path")
                    self.last["redirects"].append({"status": response.status_code, "destination": destination})
                    current = destination
                    continue
                if response.status_code != 200:
                    raise RuntimeError("http_" + str(response.status_code))
                if "html" not in response.headers.get("Content-Type", "").lower():
                    raise RuntimeError("unexpected_content_type")
                cap = min(MAX_PAGE_BYTES, MAX_BYTES - self.bytes)
                length = response.headers.get("Content-Length")
                if length and int(length) > cap:
                    raise RuntimeError("object_exceeds_budget")
                chunks, size = [], 0
                while size < cap:
                    chunk = response.raw.read(min(16_384, cap - size), decode_content=True)
                    if not chunk:
                        return b"".join(chunks).decode("utf-8", errors="replace"), current
                    size += len(chunk)
                    self.bytes += len(chunk)
                    chunks.append(chunk)
                raise RuntimeError("stream_reaches_budget")
        raise RuntimeError("redirect_limit")


def write_and_package(output, dist, rows, audit, summary):
    require_github_hosted_runner()
    output.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)
    csv_path = output / "appointments.csv.gz"
    with gzip.open(csv_path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: r"\N" if value is None else value for key, value in row.items()})
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output / "source_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    (output / "schema.json").write_text(json.dumps({"columns": COLUMNS, "null_sentinel": r"\N", "booleans": ["True", "False"], "ids_are_strings": True,
        "event_date_semantics": "Official announcement date, not employment commencement", "raw_article_content_stored": False}, indent=2) + "\n")
    (output / "RIGHTS.md").write_text("# Source rights and provenance\n\n" + RIGHTS + ". Source URLs, HTTP outcomes, retrieval times and SHA256 hashes are in source_audit.json and appointments.csv.gz. Source hashes cover decoded text encoded as UTF-8; evidence hashes cover normalized article-block text encoded as UTF-8. Neither is a wire-payload checksum. No article bodies, quotes or images are redistributed. The repository code license does not grant source-content rights. These are announcement annotations, not complete coaching tenures or verified historical features.\n")
    names = ["appointments.csv.gz", "summary.json", "source_audit.json", "schema.json", "RIGHTS.md"]
    files = []
    archive = dist / "csv-coach-appointments.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
        for name in names:
            path = output / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Invalid publication member")
            member = "data/coach_events/" + name
            tar.add(path, arcname=member, recursive=False)
            files.append({"path": member, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    size = archive.stat().st_size
    if size > 20_000_000:
        raise ValueError("Publication bound exceeded")
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = {"archive": archive.name, "bytes": size, "sha256": archive_hash, "files": files,
                "assets": [{"name": archive.name, "bytes": size, "sha256": archive_hash}], "source_rights": RIGHTS}
    (dist / "coach_appointments_asset_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copyfile(output / "summary.json", dist / "coach_appointments_summary.json")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(ROOT / "data/coach_events"))
    parser.add_argument("--package-dir", default=str(ROOT / "dist"))
    args = parser.parse_args(argv)
    require_github_hosted_runner()
    import requests
    rows, audit = [], []
    with requests.Session() as session:
        session.headers["User-Agent"] = "SportsResearchCitedMetadata/1.0 (+https://github.com/kennynakao/Tabular-Model-for-sports)"
        fetcher = Fetcher(session)
        for source in SOURCES:
            before_requests, before_bytes = fetcher.requests, fetcher.bytes
            item = {"source_id": source["id"], "sport": source["sport"], "url": source["url"], "source_kind": source["kind"], "rows": 0, "rights_status": RIGHTS}
            try:
                html, retrieved_url = fetcher.get(source["url"])
                retrieved = datetime.now(timezone.utc).isoformat()
                doc = document(html, retrieved_url)
                found, status = extract(doc, source, html, retrieved, retrieved_url)
                rows.extend(found)
                item.update(status=status, rows=len(found), retrieved_at_utc=retrieved, source_sha256=sha(html), source_hash_basis="decoded_text_utf8", article_blocks=len(doc["blocks"]),
                            publication_precision=doc["publication_precision"], publication_candidates=doc["publication_candidates"])
            except Exception as error:
                reason = str(error) if isinstance(error, RuntimeError) and re.fullmatch(r"[a-z_0-9]+", str(error)) else "details_suppressed"
                item.update(status="collection_or_parse_failed", reason=reason, error_type=type(error).__name__)
            item.update(fetcher.last, requests=fetcher.requests - before_requests, downloaded_bytes=fetcher.bytes - before_bytes)
            audit.append(item)
        safe = ["source_id", "sport", "status", "rows", "http_status", "reason", "error_type", "requests", "downloaded_bytes", "article_blocks", "publication_precision"]
        summary = {"status": "completed" if rows else "audit_only", "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "sources": len(SOURCES), "requests": fetcher.requests, "downloaded_bytes": fetcher.bytes,
            "limits": {"requests": MAX_REQUESTS, "bytes": MAX_BYTES, "page_bytes": MAX_PAGE_BYTES, "redirects_per_source": MAX_REDIRECTS},
            "annotation_rows": len(rows), "dated_announcement_rows": sum(row["event_date"] is not None for row in rows),
            "by_sport": dict(Counter(row["sport"] for row in rows)), "by_event_type": dict(Counter(row["event_type"] for row in rows)),
            "date_conflict_rows": sum(row["date_conflict_status"] != "none" for row in rows),
            "source_status_counts": dict(Counter(item["status"] for item in audit)),
            "source_results": [{key: item[key] for key in safe if key in item} for item in audit],
            "known_effective_start_rows": 0, "known_effective_end_rows": 0, "matched_game_rows": 0, "automatic_training_eligible_rows": 0,
            "historical_availability_verified_rows": 0, "article_bodies_redistributed": False, "rights_status": RIGHTS,
            "limitations": ["Announced appointments and agreements are different event types; neither proves first coaching duty or full tenure",
                            "Publication dates are current source labels, not independently verified historical availability",
                            "Effective start/end dates and canonical identities remain unknown; no automatic model joins",
                            "Small selected source catalog is not complete coaching coverage"]}
    rows.sort(key=lambda row: (row["event_date"] is None, row["event_date"] or "", row["sport"], row["team_name_as_reported"], row["source_id"]))
    write_and_package(Path(args.output_dir).resolve(), Path(args.package_dir).resolve(), rows, audit, summary)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
