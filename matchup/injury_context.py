"""Hosted-only NBA/NFL team injury report research views from published CSVs."""
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

REPO = "Spoofyy-1/Tabular-Model-for-sports"
SOURCES = [
    ("nba", "enrichment-36170742592-1", "csv-nba_injuries.tar.gz", "nba_injuries_enrichment_asset_manifest.json", "data/enrichment/nba_injuries/injury_report_entries.csv.gz"),
    ("nfl_historical", "csv-36170200182-1", "csv-base-nfl.tar.gz", "base-nfl_csv_asset_manifest.json", "data/nfl/injuries.csv.gz"),
    ("nfl_recent", "csv-36170200182-1", "csv-context-nfl.tar.gz", "context-nfl_csv_asset_manifest.json", "data/context/nfl/injuries_2025_onward.csv.gz"),
]
TEAM_NAMES = dict(zip(
    ["Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets", "Chicago Bulls", "Cleveland Cavaliers", "Dallas Mavericks", "Denver Nuggets", "Detroit Pistons", "Golden State Warriors", "Houston Rockets", "Indiana Pacers", "LA Clippers", "Los Angeles Lakers", "Memphis Grizzlies", "Miami Heat", "Milwaukee Bucks", "Minnesota Timberwolves", "New Orleans Pelicans", "New York Knicks", "Oklahoma City Thunder", "Orlando Magic", "Philadelphia 76ers", "Phoenix Suns", "Portland Trail Blazers", "Sacramento Kings", "San Antonio Spurs", "Toronto Raptors", "Utah Jazz", "Washington Wizards"],
    ["ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS"]))
TEAM_NAMES["Los Angeles Clippers"] = "LAC"
STATUSES = ["out", "doubtful", "questionable", "probable", "available"]


def split(stamp):
    if pd.isna(stamp):
        return "unresolved_game_time"
    return "fit_pre_2024" if stamp < pd.Timestamp("2024-01-01", tz="UTC") else "calibration_2024" if stamp < pd.Timestamp("2025-01-01", tz="UTC") else "holdout_2025_plus"


def status_counts(frame, column, allow_counts=True):
    values = frame[column].astype("string").str.strip().str.lower()
    return {"listed_" + status + "_count": int(values.eq(status).sum()) if allow_counts else None for status in STATUSES}


def nba_views(source):
    required = {"source_url", "espn_game_id", "team_source", "matchup_key", "player_name_source", "participation_status_source", "entry_type", "report_timestamp_utc", "game_date"}
    if not required.issubset(source):
        raise ValueError("NBA injury input schema mismatch")
    frame = source.copy().drop_duplicates()
    frame["report_timestamp_utc"] = pd.to_datetime(frame.report_timestamp_utc, utc=True, errors="coerce")
    frame["game_date"] = pd.to_datetime(frame.game_date, utc=True, errors="coerce")
    rows = []
    for keys, group in frame.groupby(["source_url", "espn_game_id", "team_source"], dropna=False, sort=True):
        url, game_id, source_team = keys
        team = TEAM_NAMES.get(str(source_team))
        matchups = group.matchup_key.dropna().unique()
        team_valid = len(matchups) == 1 and team is not None and team in str(matchups[0]).split("@")
        stamps, starts = group.report_timestamp_utc.dropna().unique(), group.game_date.dropna().unique()
        report_time = pd.Timestamp(stamps[0]) if len(stamps) == 1 else pd.NaT
        start = pd.Timestamp(starts[0]) if len(starts) == 1 else pd.NaT
        players = group.loc[group.entry_type.eq("player_status")].copy()
        # Collapse identical player status facts, excluding every conflicting identity.
        fact_cols = ["player_name_source", "participation_status_source"]
        if "reason_source" in players:
            fact_cols.append("reason_source")
        players = players.drop_duplicates(fact_cols)
        conflict = players.duplicated("player_name_source", keep=False) | players.player_name_source.isna() | players.player_name_source.eq("")
        clean = players.loc[~conflict]
        not_submitted = bool(group.entry_type.eq("team_not_submitted").any())
        upstream_excluded = bool(group.upstream_source_report_has_quarantined_rows.any()) if "upstream_source_report_has_quarantined_rows" in group else False
        valid = team_valid and pd.notna(game_id) and str(game_id) != "" and pd.notna(start) and pd.notna(report_time)
        counts_allowed = valid and not not_submitted and not bool(conflict.any()) and not upstream_excluded
        row = {"sport": "NBA", "game_id": game_id, "team": team, "team_source": source_team,
               "game_date": start, "source_report_time_utc": report_time, "source_url": url,
               "source_rows": len(group), "unique_unambiguous_listed_names": len(clean),
               "conflicting_or_missing_player_name_rows": int(conflict.sum()), "team_not_submitted": not_submitted,
               "upstream_source_report_has_quarantined_rows": upstream_excluded,
               "team_game_identity_valid": valid, "counts_complete_for_observed_report": counts_allowed,
               "report_before_start": bool(valid and report_time < start), "evaluation_split": split(start),
               "source_timing_kind": "report_header_not_verified_publication", "historical_availability_verified": False,
               "absence_means_healthy": False, "automatic_training_join_allowed": False,
               "status_counts_are_player_value_weighted": False}
        row.update(status_counts(clean, "participation_status_source", counts_allowed))
        rows.append(row)
    snapshots = pd.DataFrame(rows)
    if snapshots.empty:
        raise ValueError("No NBA report groups")
    candidates = []
    # Establish the newest observed report before deciding whether it can be
    # summarized. Filtering missing/conflicting reports first silently revives
    # an older status and makes it look current at the requested cutoff.
    valid = snapshots.loc[snapshots.team_game_identity_valid]
    for _, group in valid.groupby(["game_id", "team"]):
        if group.game_date.nunique(dropna=False) != 1:
            continue
        for minutes in [30, 60, 120]:
            cutoff = group.game_date.iloc[0] - pd.Timedelta(minutes=minutes)
            eligible = group.loc[group.source_report_time_utc < cutoff].sort_values("source_report_time_utc")
            if eligible.empty:
                continue
            latest = eligible.loc[eligible.source_report_time_utc.eq(eligible.source_report_time_utc.max())]
            if len(latest) != 1:
                continue
            item = latest.iloc[0].to_dict()
            if not item["counts_complete_for_observed_report"]:
                continue
            item.update(candidate_cutoff_minutes_before_start=minutes, candidate_cutoff_utc=cutoff,
                        report_age_hours_at_cutoff=(cutoff - item["source_report_time_utc"]).total_seconds() / 3600)
            candidates.append(item)
    return snapshots, pd.DataFrame(candidates, columns=list(snapshots.columns) + ["candidate_cutoff_minutes_before_start", "candidate_cutoff_utc", "report_age_hours_at_cutoff"])


def nfl_view(source, era):
    required = {"game_id", "team", "player_id", "report_status", "game_date"}
    if not required.issubset(source):
        raise ValueError("NFL injury input schema mismatch")
    frame = source.copy().drop_duplicates()
    frame["game_date"] = pd.to_datetime(frame.game_date, utc=True, errors="coerce")
    time_column = "source_snapshot_at_utc" if "source_snapshot_at_utc" in frame else "date_modified"
    frame["record_modified_utc"] = pd.to_datetime(frame[time_column], utc=True, errors="coerce") if time_column in frame else pd.NaT
    rows = []
    for (game_id, team), group in frame.groupby(["game_id", "team"], dropna=False, sort=True):
        starts = group.game_date.dropna().unique()
        start = pd.Timestamp(starts[0]) if len(starts) == 1 else pd.NaT
        identity = pd.notna(game_id) and str(game_id).strip() != "" and pd.notna(team) and str(team).strip() != "" and pd.notna(start)
        keys = [c for c in ["player_id", "report_status", "practice_status", "report_primary_injury", "report_secondary_injury", "practice_primary_injury", "practice_secondary_injury", "record_modified_utc"] if c in group]
        players = group.drop_duplicates(keys)
        conflict = players.duplicated("player_id", keep=False) | players.player_id.isna() | players.player_id.eq("")
        clean = players.loc[~conflict]
        counts_allowed = identity and not bool(conflict.any())
        times = group.record_modified_utc
        all_before = bool(identity and times.notna().all() and times.lt(start).all())
        # A date-only kickoff cannot establish a report was before exact kickoff.
        if "game_date_time_known" in group:
            all_before = all_before and bool(group.game_date_time_known.astype(str).str.lower().eq("true").all())
        else:
            all_before = False
        row = {"sport": "NFL", "game_id": game_id, "team": team, "game_date": start, "source_era": era,
               "source_rows": len(group), "unique_unambiguous_listed_player_ids": len(clean),
               "conflicting_or_missing_player_id_rows": int(conflict.sum()),
               "team_game_identity_valid": identity, "counts_complete_for_observed_final_snapshot": counts_allowed,
               "rows_with_unknown_report_status": int(clean.report_status.isna().sum() + clean.report_status.eq("").sum()),
               "earliest_record_modified_utc": times.min(), "latest_record_modified_utc": times.max(),
               "rows_missing_modification_time": int(times.isna().sum()), "all_modifications_before_exact_start": all_before,
               "evaluation_split": split(start), "source_timing_kind": "final_snapshot_record_modification_not_verified_publication",
               "historical_availability_verified": False, "automatic_training_join_allowed": False,
               "absence_means_healthy": False, "status_counts_are_player_value_weighted": False,
               "unknown_roster_completeness": True}
        row.update(status_counts(clean, "report_status", counts_allowed))
        if "practice_status" in clean:
            row["listed_practice_status_counts_json"] = json.dumps(clean.practice_status.fillna("<missing>").value_counts().to_dict())
        rows.append(row)
    return pd.DataFrame(rows)


def get_metadata(url):
    require_github_hosted_runner()
    with requests.get(url, stream=True, timeout=(15, 60)) as response:
        response.raise_for_status()
        data = bytearray()
        for block in response.iter_content(65536):
            data.extend(block)
            if len(data) > 2_000_000:
                raise ValueError("Metadata input exceeds bound")
    return json.loads(data)


def load_source(spec, cache):
    require_github_hosted_runner()
    label, tag, name, manifest_name, requested = spec
    release = get_metadata("https://api.github.com/repos/" + REPO + "/releases/tags/" + tag)
    assets = {a["name"]: a for a in release["assets"]}
    asset = assets[name]
    manifest = get_metadata(assets[manifest_name]["browser_download_url"])
    declared = next(a for a in manifest["assets"] if a["name"] == name)
    if not 0 < asset["size"] < 100_000_000 or declared["bytes"] != asset["size"]:
        raise ValueError("Source archive exceeds bound or disagrees with manifest")
    path = cache / name
    sha, size = hashlib.sha256(), 0
    with requests.get(asset["browser_download_url"], stream=True, timeout=(15, 120)) as response:
        response.raise_for_status()
        with path.open("wb") as stream:
            for block in response.iter_content(1024 * 1024):
                size += len(block)
                if size > asset["size"]:
                    raise ValueError("Archive transfer exceeds declared size")
                sha.update(block)
                stream.write(block)
    if size != asset["size"] or sha.hexdigest() != declared["sha256"] or asset.get("digest") != "sha256:" + sha.hexdigest():
        raise ValueError("Archive hash verification failed")
    with tarfile.open(path, "r:gz") as archive:
        members = [m for m in archive.getmembers() if m.name == requested]
        if len(members) != 1:
            raise ValueError("Expected injury CSV member missing or duplicated: " + requested)
        member = members[0]
        part = PurePosixPath(member.name)
        if not member.isfile() or member.issym() or member.islnk() or part.is_absolute() or ".." in part.parts or member.size > 60_000_000:
            raise ValueError("Unsafe or oversized injury table")
        with gzip.GzipFile(fileobj=archive.extractfile(member)) as stream:
            frame = pd.read_csv(stream, dtype="string", keep_default_na=False, na_values=[r"\N"])
        if label == "nba":
            excluded_member = archive.getmember("data/enrichment/nba_injuries/quarantined_rows.csv.gz")
            if not excluded_member.isfile() or excluded_member.size > 5_000_000:
                raise ValueError("Unexpected NBA quarantine member")
            with gzip.GzipFile(fileobj=archive.extractfile(excluded_member)) as stream:
                excluded = pd.read_csv(stream, dtype="string", keep_default_na=False, na_values=[r"\N"])
            if "source_url" not in excluded:
                raise ValueError("NBA quarantine lacks source report identity")
            frame["upstream_source_report_has_quarantined_rows"] = frame.source_url.isin(set(excluded.source_url.dropna()))
    path.unlink()
    return frame, {"label": label, "release_tag": tag, "url": asset["browser_download_url"], "archive_sha256": sha.hexdigest(),
                   "source_bytes": size, "selected_member": requested, "rows": len(frame)}


def write(frame, path):
    require_github_hosted_runner()
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"file": path.name, "rows": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "columns": {c: str(t) for c, t in frame.dtypes.items()}, "missing": {c: int(n) for c, n in frame.isna().sum().items()}}


def main():
    require_github_hosted_runner()
    output = ROOT / "data/matchup/injury_context"
    output.mkdir(parents=True, exist_ok=True)
    sources, nfl_parts, tables = [], [], {}
    with tempfile.TemporaryDirectory(prefix="team-injuries-", dir=os.environ["RUNNER_TEMP"]) as temp:
        for spec in SOURCES:
            frame, provenance = load_source(spec, Path(temp))
            sources.append(provenance)
            if sum(item["source_bytes"] for item in sources) > 180_000_000:
                raise ValueError("Injury source total exceeds bound")
            if spec[0] == "nba":
                snapshots, candidates = nba_views(frame)
                tables["nba_team_report_snapshots"] = write(snapshots, output / "nba_team_report_snapshots.csv.gz")
                tables["nba_cutoff_candidates_unverified"] = write(candidates, output / "nba_cutoff_candidates_unverified.csv.gz")
            else:
                nfl_parts.append(nfl_view(frame, spec[0]))
            print(json.dumps({"source": spec[0], "input_rows": len(frame)}), flush=True)
    nfl = pd.concat(nfl_parts, ignore_index=True).sort_values(["game_date", "game_id", "team", "source_era"], na_position="last")
    tables["nfl_team_final_reports_unverified"] = write(nfl, output / "nfl_team_final_reports_unverified.csv.gz")
    summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "sources": sources, "tables": tables,
        "nba_team_report_snapshots": len(snapshots), "nba_cutoff_candidate_rows_overlapping_windows": len(candidates),
        "nba_valid_team_game_identities": int(snapshots.team_game_identity_valid.sum()),
        "nba_not_submitted_snapshots": int(snapshots.team_not_submitted.sum()),
        "nba_snapshots_affected_by_source_quarantine": int(snapshots.upstream_source_report_has_quarantined_rows.sum()),
        "nfl_team_game_reports": len(nfl), "nfl_reports_with_all_modifications_before_start": int(nfl.all_modifications_before_exact_start.sum()),
        "nfl_reports_with_missing_modification_times": int(nfl.rows_missing_modification_time.gt(0).sum()),
        "nba_split_counts": snapshots.evaluation_split.value_counts().to_dict(), "nfl_split_counts": nfl.evaluation_split.value_counts().to_dict(),
        "automatic_model_integration": False, "source_bytes": sum(s["source_bytes"] for s in sources),
        "limitations": ["A team report records listed players only; it is not a complete eligible roster and missing rows never mean healthy.",
            "Counts are not weighted by player quality, expected minutes or usage. NBA player identities remain unresolved names.",
            "NBA cutoff views use labeled report times, not independently verified historical publication. Overlapping cutoff windows are not independent games.",
            "NFL exports are final report snapshots with per-record modification times. They cannot reconstruct earlier report states or prove simultaneous public availability.",
            "No automatic injury feature join or training is enabled. Report gaps, unresolved identities and conflicting players remain flagged."]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    (output / "schema.json").write_text(json.dumps({"csv_null_encoding": r"\N", "tables": tables, "team_name_exact_aliases": TEAM_NAMES}, indent=2))
    (output / "SOURCE_RIGHTS.md").write_text("# Injury context provenance\n\nNFL injury facts via nflverse, CC BY 4.0: https://nflreadr.nflverse.com/articles/dictionary_injuries.html . Preserve the original release attribution and source provenance. NBA factual report metadata derives from official injury PDFs: https://official.nba.com/nba-injury-report-2025-26-season/ . The upstream parser's MIT license does not license NBA content; no original PDFs or narrative bodies are redistributed and no blanket commercial rights are granted. These team aggregates do not create additional source rights. See source release URLs and hashes in summary.json.\n")


if __name__ == "__main__":
    main()
