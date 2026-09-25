"""Bounded hosted-only primary-source staff and preparation annotations.

Only short normalized facts and provenance leave memory. Advice, a stated plan,
and a reported habit remain distinct. None establishes a performance effect.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

MAX_REQUESTS = 20
MAX_BYTES = 20_000_000
MAX_OBJECT_BYTES = 4_000_000
RIGHTS = "No open article-content license identified; short cited factual annotations only; commercial reuse not cleared"
# Source configuration contains URLs and parser choices, never seeded fact rows.
SOURCES = [
    {"source_id": "nets_staff_announcement", "sport": "NBA", "organization": "Brooklyn Nets", "parser": "nets_dietitian",
     "url": "https://www.nba.com/nets/news/brooklyn-nets-announce-staff-additions-and-promotions-2024"},
    {"source_id": "sixers_current_directory", "sport": "NBA", "organization": "Philadelphia 76ers", "parser": "sixers_directory",
     "url": "https://www.nba.com/sixers/team/staff-directory"},
    {"source_id": "nba_conference_nutrition_profile", "sport": "NBA", "organization": "Phoenix Suns", "parser": "nba_conference_dietitian",
     "url": "https://healthandperformancemeetings.nba.com/participants/jesse-mcginley/"},
    {"source_id": "nba_conference_coach_profile", "sport": "NBA", "organization": "Charlotte Hornets", "parser": "nba_conference_coach",
     "url": "https://healthandperformancemeetings.nba.com/participants/charles-lee/"},
    {"source_id": "broncos_nutrition_seminar", "sport": "NFL", "organization": "Denver Broncos", "parser": "broncos_seminar",
     "url": "https://www.denverbroncos.com/news/rookie-seminar-nutrition-with-bryan-snyder-17186893"},
    {"source_id": "atp_final_preparation", "sport": "tennis", "organization": None, "parser": "atp_final_preparation",
     "url": "https://www.atptour.com/en/news/michael-russell-us-open-2024-final-preview"},
    {"source_id": "atp_team_routine", "sport": "tennis", "organization": None, "parser": "atp_team_routine",
     "url": "https://www.atptour.com/en/news/paul-miami-2024-feature"},
]

COLUMNS = ["annotation_id", "sport", "organization", "subject_name_as_reported", "subject_kind", "staff_name_as_reported",
           "annotation_type", "role_code", "routine_code", "assertion_kind", "observation_date", "observation_date_precision",
           "staff_valid_from", "staff_valid_from_precision", "staff_valid_to", "staff_valid_to_precision",
           "source_id", "source_url", "source_published_date", "source_published_at_utc", "publication_precision", "publication_basis",
           "source_modified_date", "source_modified_at_utc", "retrieved_at_utc", "article_sha256", "evidence_sha256", "extraction_rule",
           "historical_publication_verified", "identity_independently_verified", "match_adherence_observed",
           "automatic_training_join_allowed", "performance_effect_established", "rights_status"]
MONTHS = {name.lower(): i for i, name in enumerate(["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]) if name}
MONTHS.update({name[:3]: i for name, i in list(MONTHS.items())})
DATE_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\.?\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
NAME = r"[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’-]+(?: [A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'’-]+){1,3}"


def clean(value):
    return re.sub(r"\s+", " ", str(value)).strip().replace("’", "'")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def walk(value):
    if isinstance(value, dict):
        yield value
        for key, child in value.items():
            if key.lower() not in {"related", "relatedarticles", "recommendations", "relatedstories"}:
                yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def date_fields(value):
    """Naive timestamps lose time precision instead of acquiring a fake timezone."""
    text = clean(value)
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (stamp.date().isoformat(), stamp.astimezone(timezone.utc).isoformat(), "timestamp_with_timezone") if stamp.tzinfo else (stamp.date().isoformat(), None, "date_only")
    except ValueError:
        pass
    match = DATE_RE.search(text)
    if not match:
        return None, None, "unknown"
    try:
        day = datetime(int(match[3]), MONTHS[match[1].lower()], int(match[2]))
        clock = re.search(r"\b(\d{1,2}):(\d{2})\s*(AM|PM)\s+(EDT|EST|UTC|GMT)\b", text, re.I)
        if clock:
            hour = int(clock[1]) % 12 + (12 if clock[3].upper() == "PM" else 0)
            if not 1 <= int(clock[1]) <= 12:
                raise ValueError("Invalid hour")
            offset = {"EDT": -4, "EST": -5, "UTC": 0, "GMT": 0}[clock[4].upper()]
            stamp = day.replace(hour=hour, minute=int(clock[2]), tzinfo=timezone(timedelta(hours=offset)))
            return day.date().isoformat(), stamp.astimezone(timezone.utc).isoformat(), "timestamp_with_timezone"
        return day.date().isoformat(), None, "date_only"
    except ValueError:
        return None, None, "unknown"


def parse_document(html, url):
    """Scope evidence to an article/structured article, never sitewide text."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    candidates, modified, bodies = [], [], []
    for node in soup.find_all("meta"):
        label = str(node.get("property") or node.get("name") or "").lower()
        if label in {"article:published_time", "datepublished", "date", "pubdate"}:
            candidates.append((node.get("content", ""), "meta_publication"))
        elif label in {"article:modified_time", "datemodified"}:
            modified.append(node.get("content", ""))
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    for script in soup.find_all("script"):
        if script.get("type") != "application/ld+json" and script.get("id") != "__NEXT_DATA__":
            continue
        try:
            nodes = walk(json.loads(script.get_text()))
            for node in nodes:
                type_value = node.get("@type", "")
                types = type_value if isinstance(type_value, list) else [type_value]
                linked = node.get("url") or node.get("@id") or node.get("mainEntityOfPage")
                if isinstance(linked, dict):
                    linked = linked.get("@id")
                same_url = isinstance(linked, str) and urlparse(linked).path.rstrip("/") == urlparse(url).path.rstrip("/")
                # A linked conflicting Article is never evidence for this page.
                article = any(t in {"Article", "NewsArticle", "BlogPosting"} for t in types) and (not linked or same_url)
                scoped = same_url or node.get("slug") == slug
                if article or scoped:
                    if node.get("datePublished"):
                        candidates.append((node["datePublished"], "structured_publication"))
                    if node.get("dateModified"):
                        modified.append(node["dateModified"])
                    for key in ("articleBody", "body", "content", "contentHtml"):
                        body = node.get(key)
                        if isinstance(body, dict):
                            body = body.get("rendered")
                        if isinstance(body, str) and len(body) <= 500_000:
                            bodies.append(body)
        except (ValueError, TypeError, RecursionError):
            continue
    containers = soup.select("article, [itemprop='articleBody'], .nfl-c-body-part, .atp_article, .article-content, .article-body")
    if not containers:
        # Source-specific NBA/ATP page layouts: still reject navigation/related.
        containers = soup.select("[class*='Article_article'], [class*='ArticleContent'], .news-article")
    for container in containers:
        for junk in container.select("nav, footer, aside, script, .related-content, [class*='related']"):
            junk.decompose()
        bodies.append(str(container))
    blocks = []
    for body in dict.fromkeys(bodies):
        fragment = BeautifulSoup(body, "html.parser")
        nodes = fragment.find_all(["p", "h1", "h2", "h3", "li", "tr"])
        if nodes:
            blocks.extend(clean(node.get_text(" ", strip=True)) for node in nodes)
        else:
            blocks.extend(clean(line) for line in fragment.get_text("\n", strip=True).splitlines())
    profile_names = []
    # The official conference site can render a participant as a WordPress
    # excerpt. A name must be tied to the exact profile URL, then the role must
    # occur in a paragraph attributed to that name and the configured team.
    parsed_url = urlparse(url)
    if parsed_url.hostname == "healthandperformancemeetings.nba.com" and parsed_url.path.startswith("/participants/"):
        for anchor in soup.find_all("a", href=True):
            target = urlparse(urljoin(url, anchor["href"]))
            name = clean(anchor.get_text(" ", strip=True))
            if target.hostname == parsed_url.hostname and target.path.rstrip("/") == parsed_url.path.rstrip("/") and re.fullmatch(NAME, name):
                profile_names.append(name)
        for heading in soup.find_all(["h1", "h2"]):
            name = clean(heading.get_text(" ", strip=True))
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if re.fullmatch(NAME, name) and slug == parsed_url.path.rstrip("/").rsplit("/", 1)[-1]:
                profile_names.append(name)
        for paragraph in soup.find_all("p"):
            if not paragraph.find_parent(["nav", "footer", "aside"]):
                blocks.append(clean(paragraph.get_text(" ", strip=True)))
    # Undated directories are observed only at retrieval, never backfilled.
    tables = [[clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])] for tr in soup.find_all("tr")]
    for time in soup.find_all("time"):
        candidates.append((time.get("datetime") or time.get_text(" ", strip=True), "time_element"))
    # Only dedicated date labels; dates mentioned in prose are not publication.
    for node in soup.select(".nfl-c-article__date, .article-date, .date, [class*='ArticleDate'], [class*='ContentHeader_date']"):
        candidates.append((node.get_text(" ", strip=True), "visible_publication_label"))
    publication = {"source_published_date": None, "source_published_at_utc": None, "publication_precision": "unknown", "publication_basis": None,
                   "source_modified_date": None, "source_modified_at_utc": None}
    for value, basis in candidates:
        day, stamp, precision = date_fields(value)
        if day:
            publication.update(source_published_date=day, source_published_at_utc=stamp, publication_precision=precision, publication_basis=basis)
            break
    for value in modified:
        day, stamp, _ = date_fields(value)
        if day:
            publication.update(source_modified_date=day, source_modified_at_utc=stamp)
            break
    return {"blocks": list(dict.fromkeys(filter(None, blocks))), "tables": tables,
            "profile_names": list(dict.fromkeys(profile_names)), **publication}


def extract_facts(document, source):
    """Capture identities from evidence; absence of an exact pattern yields no row."""
    blocks = document["blocks"]
    text = " ".join(blocks)
    facts = []

    def add(evidence, kind, role=None, routine=None, staff=None, subject=None, subject_kind="team", assertion="role_reported"):
        facts.append({"subject_name_as_reported": subject or source["organization"], "subject_kind": subject_kind,
                      "staff_name_as_reported": staff, "annotation_type": kind, "role_code": role, "routine_code": routine,
                      "assertion_kind": assertion, "evidence_sha256": digest(evidence), "extraction_rule": source["parser"]})

    parser = source["parser"]
    if parser == "nets_dietitian":
        for block in blocks:
            match = re.search(r"\b(" + NAME + r") begins her first season with the organization as the team's performance dietitian\b", block)
            if match:
                add(block, "staff_role", role="performance_dietitian", staff=match[1], assertion="staff_announcement")
    elif parser == "sixers_directory":
        for row in document["tables"]:
            if len(row) == 2 and row[0].casefold() == "dietitian" and re.fullmatch(NAME, row[1]):
                add(" | ".join(row), "staff_role", role="dietitian", staff=row[1], assertion="current_directory_listing")
    elif parser in {"nba_conference_dietitian", "nba_conference_coach"}:
        names = document.get("profile_names", [])
        if len(names) == 1:
            name = names[0]
            organization = re.escape(source["organization"])
            for block in blocks:
                if parser == "nba_conference_dietitian":
                    attributed = block.startswith(name + " ") or block.startswith(name.split()[0] + " ")
                    matched = re.search(r"\bcurrently serving as the Performance Dietitian for the " + organization + r"\b", block)
                    role = "performance_dietitian"
                else:
                    attributed = block.startswith(name + " ")
                    matched = re.search(r"\bat the helm of the " + organization + r" after being hired as the \d+(?:st|nd|rd|th) head coach\b", block)
                    role = "head_coach"
                if attributed and matched:
                    add(block, "staff_role", role=role, staff=name, assertion="undated_profile_role_reported")
    elif parser == "broncos_seminar":
        matches = [re.search(r"Director of Team Nutrition (" + NAME + r")", b) for b in blocks]
        matches = [m for m in matches if m]
        if len({m[1] for m in matches}) == 1:
            staff = matches[0][1]
            speaker = staff.split()[-1]
            evidence = next(b for b in blocks if "Director of Team Nutrition " + staff in b)
            add(evidence, "staff_role", role="director_of_team_nutrition", staff=staff)
            for block in blocks:
                attributed = re.search(r"\b" + re.escape(speaker) + r"\b", block)
                if "small-to-medium size meal a few hours before the game" in block and attributed:
                    add(block, "routine_mention", routine="pregame_meal_timing", staff=staff, subject=source["organization"] + " rookies", subject_kind="team_subgroup", assertion="advice_reported")
                if "encouraged the rookies" in block and "during games" in block and "snacks" in block and attributed:
                    add(block, "routine_mention", routine="in_game_snack_availability", staff=staff, subject=source["organization"] + " rookies", subject_kind="team_subgroup", assertion="advice_reported")
    elif parser == "atp_final_preparation":
        athlete = re.search(r"\b(" + NAME + r") earned the biggest win of his career", text)
        coach = re.search(r"\bled by coach (" + NAME + r")", text)
        if athlete and coach:
            name, staff = athlete[1], coach[1]
            add(next(b for b in blocks if "led by coach " in b), "staff_role", role="coach", staff=staff, subject=name, subject_kind="athlete")
            for block in blocks:
                if "footage he would watch" in block and "analytics he would look at" in block:
                    add(block, "routine_mention", routine="opponent_video_analytics_preparation", staff=staff, subject=name, subject_kind="athlete", assertion="plan_reported")
                if "light practice tomorrow afternoon" in block:
                    add(block, "routine_mention", routine="light_practice_before_final", staff=staff, subject=name, subject_kind="athlete", assertion="plan_reported")
    elif parser == "atp_team_routine":
        athlete = re.search(r"\b(" + NAME + r") should feel right at home in Miami", text)
        if athlete:
            name = athlete[1]
            for block in blocks:
                role = re.search(r"His fitness coach (" + NAME + r") and physio (" + NAME + r") are natives", block)
                if role:
                    add(block, "staff_role", role="fitness_coach", staff=role[1], subject=name, subject_kind="athlete")
                    add(block, "staff_role", role="physiotherapist", staff=role[2], subject=name, subject_kind="athlete")
                if "team breakfasts" in block and "include mate" in block:
                    add(block, "routine_mention", routine="team_breakfast_mate", subject=name, subject_kind="athlete_team", assertion="habit_reported")
                if "trying to imitate Maradona" in block and "before a practice session or a match" in block:
                    add(block, "routine_mention", routine="football_juggling_warmup", subject=name, subject_kind="athlete", assertion="habit_reported")
    # Repeated quotes/HTML and structured copies do not inflate annotation counts.
    unique = {}
    for fact in facts:
        key = tuple(fact[k] for k in ("subject_name_as_reported", "staff_name_as_reported", "annotation_type", "role_code", "routine_code", "assertion_kind"))
        unique.setdefault(key, fact)
    return list(unique.values())


def annotate(document, source, html, retrieved):
    rows = []
    for fact in extract_facts(document, source):
        row = {key: None for key in COLUMNS}
        row.update(fact)
        row.update({key: document[key] for key in ("source_published_date", "source_published_at_utc", "publication_precision", "publication_basis", "source_modified_date", "source_modified_at_utc")})
        row.update(sport=source["sport"], organization=source["organization"], source_id=source["source_id"], source_url=source["url"],
                   observation_date_precision="unknown", staff_valid_from_precision="unknown", staff_valid_to_precision="unknown",
                   retrieved_at_utc=retrieved, article_sha256=digest(html), historical_publication_verified=False,
                   identity_independently_verified=False, match_adherence_observed=False, automatic_training_join_allowed=False,
                   performance_effect_established=False, rights_status=RIGHTS)
        # Directories establish only that a listing was visible at retrieval.
        # This is NOT evidence of employment at any earlier or later date.
        if fact["assertion_kind"] == "current_directory_listing":
            row.update(observation_date=retrieved[:10], observation_date_precision="retrieval_date_listing_only")
            row.update(source_published_date=None, source_published_at_utc=None, publication_precision="unknown", publication_basis=None)
        elif fact["assertion_kind"] == "undated_profile_role_reported":
            # A biography visible now can describe a previous appointment. Even
            # a dated appointment in that biography is not a verified historical
            # publication or a complete employment interval.
            row.update(observation_date=retrieved[:10], observation_date_precision="retrieval_date_profile_only")
            row.update(source_published_date=None, source_published_at_utc=None, publication_precision="unknown", publication_basis=None)
        identity = [source["source_id"]] + [fact[k] for k in ("subject_name_as_reported", "staff_name_as_reported", "role_code", "routine_code", "assertion_kind")]
        row["annotation_id"] = digest(json.dumps(identity, ensure_ascii=False))
        rows.append(row)
    return rows


class Budget:
    def __init__(self, session):
        self.session, self.requests, self.bytes = session, 0, 0
        self.last_http_status = None

    def get(self, url):
        self.last_http_status = None
        if self.requests >= MAX_REQUESTS or self.bytes >= MAX_BYTES:
            raise RuntimeError("collection_budget_exhausted")
        self.requests += 1
        # Redirects and retry challenges are audit outcomes, never bypasses.
        with self.session.get(url, timeout=(10, 35), stream=True, allow_redirects=False) as response:
            self.last_http_status = response.status_code
            if response.status_code != 200:
                raise RuntimeError("http_" + str(response.status_code))
            if "html" not in response.headers.get("Content-Type", "").lower():
                raise RuntimeError("unexpected_content_type")
            length = response.headers.get("Content-Length")
            cap = min(MAX_OBJECT_BYTES, MAX_BYTES - self.bytes)
            if length and int(length) > cap:
                raise RuntimeError("object_exceeds_budget")
            chunks, size = [], 0
            while size < cap:
                # Bound actual decompressed reads too, including absent/wrong
                # Content-Length and compressed responses. Conservatively reject
                # an object that reaches the cap without an observed EOF.
                chunk = response.raw.read(min(16_384, cap - size), decode_content=True)
                if not chunk:
                    return b"".join(chunks).decode("utf-8", errors="replace")
                size += len(chunk)
                self.bytes += len(chunk)
                chunks.append(chunk)
            raise RuntimeError("stream_reaches_budget")


def write_csv(path, rows):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "\\N" if value is None else value for key, value in row.items()})


def aggregate_source_results(audit):
    """Public operational diagnostics only: no identity or evidence rows."""
    allowed = ("source_id", "sport", "status", "rows", "http_status", "reason", "error_type", "requests", "downloaded_bytes", "extracted_article_blocks", "publication_precision")
    return [{key: item[key] for key in allowed if key in item} for item in audit]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="data/matchup/staff_routines")
    args = parser.parse_args(argv)
    require_github_hosted_runner()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows, audit = [], []
    with requests.Session() as session:
        session.headers["User-Agent"] = "SportsResearchCitedMetadata/1.0 (+https://github.com/Spoofyy-1/Tabular-Model-for-sports)"
        budget = Budget(session)
        for source in SOURCES:
            result = {**source, "status": "not_requested", "rows": 0, "rights_status": RIGHTS}
            before_requests, before_bytes = budget.requests, budget.bytes
            try:
                html = budget.get(source["url"])
                retrieved = datetime.now(timezone.utc).isoformat()
                document = parse_document(html, source["url"])
                found = annotate(document, source, html, retrieved)
                rows.extend(found)
                result.update(status="parsed" if found else "no_qualified_evidence", rows=len(found),
                              retrieved_at_utc=retrieved, article_sha256=digest(html), publication_precision=document["publication_precision"],
                              extracted_article_blocks=len(document["blocks"]))
            except Exception as exc:
                # Never log response bodies, URLs from exceptions, or source rows.
                result.update(status="collection_or_parse_failed", error_type=type(exc).__name__,
                              reason=str(exc) if isinstance(exc, RuntimeError) and re.fullmatch(r"[a-z_0-9]+", str(exc)) else "details_suppressed")
            result.update(http_status=budget.last_http_status, requests=budget.requests - before_requests,
                          downloaded_bytes=budget.bytes - before_bytes)
            audit.append(result)
        counts = Counter(row["sport"] for row in rows)
        summary = {"status": "completed" if rows else "audit_only", "requests": budget.requests, "downloaded_bytes": budget.bytes,
                   "limits": {"requests": MAX_REQUESTS, "bytes": MAX_BYTES, "object_bytes": MAX_OBJECT_BYTES},
                   "sources": len(SOURCES), "sources_with_annotations": sum(a["rows"] > 0 for a in audit),
                   "source_results": aggregate_source_results(audit),
                   "source_status_counts": dict(Counter(item["status"] for item in audit)),
                   "annotation_rows": len(rows), "by_sport": dict(counts), "by_type": dict(Counter(r["annotation_type"] for r in rows)),
                   "by_assertion_kind": dict(Counter(r["assertion_kind"] for r in rows)),
                   "known_valid_from_rows": 0, "known_valid_to_rows": 0, "automatic_training_eligible_rows": 0,
                   "matched_game_rows": 0, "historical_publication_verified_rows": 0, "performance_effect_claims": 0,
                   "article_bodies_redistributed": False, "rights_status": RIGHTS,
                   "limitations": ["Small purposefully selected source catalog, not comprehensive staff coverage",
                                   "Publication date is not role start date or observed routine date",
                                   "Current listing observed at retrieval only; no historical tenure backfill",
                                   "Reported advice, plans and habits do not establish adherence or causal performance effects",
                                   "Historical publication snapshots and canonical person/event identities remain unaudited"]}
    write_csv(output / "staff_routine_annotations.csv.gz", rows)
    (output / "source_audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    (output / "schema.json").write_text(json.dumps({"columns": COLUMNS, "null_sentinel": "\\N", "encoding": "utf-8", "source_article_text_stored": False}, indent=2) + "\n")
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
