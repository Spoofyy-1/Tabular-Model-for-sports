"""Cloud-only candidate mentions from the already published MCP research bundle."""
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime import require_github_hosted_runner

REPO = "Spoofyy-1/Tabular-Model-for-sports"
TAG = "tennis-points-36173714850-1"
ARCHIVE = "csv-tennis-mcp-research-only.tar.gz"
MANIFEST = "tennis_points_asset_manifest.json"
PREFIX = "data/tennis/mcp_research_only/"
MAX_SOURCE_BYTES = 250_000_000
LICENSE = "CC-BY-NC-SA-4.0"
RULES = {
    "medical_or_timeout": r"\b(?:medical|physio(?:therapist)?|trainer|MTO|treatment|injur(?:y|ed)|cramp(?:s|ing)?|time[ -]?out)\b",
    "weather": r"\b(?:rain(?:ing|y)?|wind(?:y)?|gust(?:s|y)?|humid(?:ity)?|heat|hot|cold|weather|sun(?:ny)?|temperature)\b",
    "roof": r"\b(?:roof|covered court|indoor(?:s)?)\b",
    "light_or_visibility": r"\b(?:light(?:s|ing)?|dark(?:ness)?|shadow(?:s)?|glare|visibility|floodlight(?:s)?)\b",
    "noise_or_crowd": r"\b(?:crowd|spectator(?:s)?|noise|noisy|heckl(?:e|er|ing)|boo(?:ing|ed)?|cheer(?:s|ing)?|shout(?:s|ing)?)\b",
    "time_violation_or_serve_clock": r"\b(?:time violation|serve clock|shot clock|time warning|slow play|delay of game|25[ -]second)\b",
    "equipment": r"\b(?:racket|racquet|string(?:s|ing)?|grip|dampener|broken racket|broken racquet|equipment)\b",
    "underarm_serve": r"\bunder[ -]?(?:arm|hand)(?:[ -]serve)?\b",
    "interruption": r"\b(?:interruption|interrupt(?:ed|ion|ions)?|delay(?:ed|s)?|suspend(?:ed)?|suspension|toilet|bathroom|change of clothes|stoppage|stop(?:ped)? play)\b",
}
COMPILED = {name: re.compile(pattern, re.I) for name, pattern in RULES.items()}
NEGATION = re.compile(r"\b(?:no|not|never|without|denied|didn't|wasn't|isn't)\b", re.I)
RETROSPECTIVE = re.compile(r"\b(?:previous|earlier|yesterday|last (?:point|game|set|match)|before this)\b", re.I)
BASE_COLUMNS = ["source_record_id", "source_member", "source_member_sha256", "source_row_number", "competition_group", "match_id", "source_point_number_raw", "point_join_key", "source_server_player_raw", "mention_category", "matched_phrases_json", "source_notes", "source_notes_sha256", "negation_word_present", "retrospective_word_present", "mention_independently_verified", "actor_resolved", "time_of_event_verified", "absence_inference_allowed", "automatic_training_join_allowed", "research_only_noncommercial", "source_release_tag"]
STATE_COLUMNS = ["match_id", "source_point_number", "evaluation_split", "pre_score_prefix_valid"]
MATCH_COLUMNS = ["match_id", "competition_group", "match_date", "source_match_date_precision", "player1_name", "player2_name", "surface", "tournament", "round", "singles_metadata_eligible", "source_retirement_or_walkover_flag"]


def point_key(value):
    if pd.isna(value):
        return None
    value = str(value).strip()
    # Canonical source exports may serialize an integral point number as 1.0.
    if not re.fullmatch(r"[0-9]+(?:\.0+)?", value):
        return None
    integer = int(value.split(".")[0])
    return str(integer) if integer >= 1 else None


def mentions(note):
    if pd.isna(note) or not str(note).strip():
        return []
    note = str(note)
    return [(category, sorted(set(match.group(0).lower() for match in pattern.finditer(note)))[:20])
            for category, pattern in COMPILED.items() if pattern.search(note)]


def mention_rows(chunk, member, member_hash, offset):
    required = {"match_id", "Pt", "Notes"}
    if not required.issubset(chunk):
        raise ValueError("Source points lack required note/point identity columns")
    parts = PurePosixPath(member).parts
    group = "mens_singles" if "mens_singles" in parts else "womens_singles" if "womens_singles" in parts else None
    if group is None:
        raise ValueError("Unknown source competition group")
    rows = []
    servers = chunk["Svr"] if "Svr" in chunk else pd.Series(pd.NA, index=chunk.index)
    values = zip(chunk["match_id"], chunk["Pt"], chunk["Notes"], servers)
    for position, (match_id, point_number, note, server) in enumerate(values, start=offset + 1):
        for category, phrases in mentions(note):
            identity = TAG + ":" + member + ":" + str(position) + ":" + category
            rows.append({"source_record_id": hashlib.sha256(identity.encode()).hexdigest(), "source_member": member,
                "source_member_sha256": member_hash, "source_row_number": position, "competition_group": group,
                "match_id": match_id, "source_point_number_raw": point_number, "point_join_key": point_key(point_number),
                "source_server_player_raw": server, "mention_category": category,
                "matched_phrases_json": json.dumps(phrases, ensure_ascii=False), "source_notes": str(note),
                "source_notes_sha256": hashlib.sha256(str(note).encode()).hexdigest(),
                "negation_word_present": bool(NEGATION.search(str(note))), "retrospective_word_present": bool(RETROSPECTIVE.search(str(note))),
                "mention_independently_verified": False, "actor_resolved": False, "time_of_event_verified": False,
                "absence_inference_allowed": False, "automatic_training_join_allowed": False,
                "research_only_noncommercial": True, "source_release_tag": TAG})
    return rows


def fetch_memory(url, limit):
    require_github_hosted_runner()
    response = requests.get(url, stream=True, timeout=(15, 60))
    with response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(1024 * 1024):
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("Source metadata exceeded bound")
    return bytes(data)


def download(cache):
    require_github_hosted_runner()
    if not cache.resolve().is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()):
        raise RuntimeError("Source cache must remain within RUNNER_TEMP")
    release = json.loads(fetch_memory("https://api.github.com/repos/" + REPO + "/releases/tags/" + TAG, 1_000_000))
    if release.get("tag_name") != TAG:
        raise ValueError("Source release tag mismatch")
    assets = {asset["name"]: asset for asset in release["assets"]}
    asset, manifest_asset = assets[ARCHIVE], assets[MANIFEST]
    if not 0 < asset["size"] <= MAX_SOURCE_BYTES:
        raise ValueError("Archive exceeds 250 MB source bound")
    manifest_bytes = fetch_memory(manifest_asset["browser_download_url"], 2_000_000)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if len(manifest_bytes) != manifest_asset["size"] or manifest_asset.get("digest") not in (None, "sha256:" + manifest_hash):
        raise ValueError("Source manifest asset integrity verification failed")
    manifest = json.loads(manifest_bytes)
    if manifest.get("archive") != ARCHIVE or manifest.get("bytes") != asset["size"] or LICENSE not in manifest.get("licenses", []):
        raise ValueError("Source manifest metadata/license mismatch")
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / ARCHIVE
    sha, size = hashlib.sha256(), 0
    with requests.get(asset["browser_download_url"], stream=True, timeout=(15, 120)) as response:
        response.raise_for_status()
        with path.open("wb") as stream:
            for block in response.iter_content(1024 * 1024):
                size += len(block)
                if size > MAX_SOURCE_BYTES or size > asset["size"]:
                    raise ValueError("Archive transfer exceeds declared bound")
                sha.update(block)
                stream.write(block)
    digest = sha.hexdigest()
    if size != asset["size"] or digest != manifest["sha256"] or asset.get("digest") not in (None, "sha256:" + digest):
        raise ValueError("Source archive integrity verification failed")
    return path, manifest, {"release_url": release["html_url"], "release_tag": TAG, "url": asset["browser_download_url"],
        "asset_id": asset["id"], "asset_updated_at": asset["updated_at"], "bytes": size, "sha256": digest,
        "manifest_url": manifest_asset["browser_download_url"], "manifest_sha256": manifest_hash}


def safe_members(tar, manifest):
    declared = {item["path"]: item for item in manifest["files"]}
    selected = {}
    for member in tar.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
            raise ValueError("Unsafe source archive member")
        if member.isfile():
            if member.name in selected:
                raise ValueError("Duplicate source archive member")
            if not member.name.startswith(PREFIX) or member.name not in declared:
                raise ValueError("Unexpected source archive member")
            if member.size != declared[member.name]["bytes"] or member.size > 100_000_000:
                raise ValueError("Archive member size mismatch or bound exceeded")
            selected[member.name] = member
    if set(selected) != set(declared):
        raise ValueError("Source archive inventory differs from manifest")
    return selected, declared


class HashReader(io.RawIOBase):
    def __init__(self, stream):
        self.stream, self.sha = stream, hashlib.sha256()

    def readable(self):
        return True

    def read(self, size=-1):
        data = self.stream.read(size)
        self.sha.update(data)
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def csv_chunks(tar, member, entry, columns=None):
    stream = HashReader(tar.extractfile(member))
    with gzip.GzipFile(fileobj=stream, mode="rb") as decompressed:
        reader = pd.read_csv(decompressed, dtype="string", keep_default_na=False, na_values=[r"\N"],
                             usecols=(lambda name: name in columns) if columns else None, chunksize=25_000)
        for chunk in reader:
            yield chunk
    # Hash every compressed source byte, including any unused trailing bytes.
    while stream.read(1024 * 1024):
        pass
    if stream.sha.hexdigest() != entry["sha256"]:
        raise ValueError("CSV member checksum failed")


def write(frame, path):
    require_github_hosted_runner()
    frame.to_csv(path, index=False, na_rep=r"\N", compression={"method": "gzip", "mtime": 0})
    return {"file": path.name, "rows": len(frame), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "columns": {name: str(dtype) for name, dtype in frame.dtypes.items()},
            "missing": {name: int(value) for name, value in frame.isna().sum().items()}}


def main():
    require_github_hosted_runner()
    cache = Path(os.environ["RUNNER_TEMP"]) / "tennis_notes_source"
    output = ROOT / "data/tennis/notes_research_only"
    output.mkdir(parents=True, exist_ok=True)
    try:
        path, manifest, provenance = download(cache)
        rows, matches_parts, states_parts, source_counts = [], [], [], []
        with tarfile.open(path, "r:gz") as tar:
            members, declared = safe_members(tar, manifest)
            for name in sorted(members):
                if name.endswith("/matches.csv.gz"):
                    matches_parts.extend(csv_chunks(tar, members[name], declared[name], MATCH_COLUMNS))
            matches = pd.concat(matches_parts, ignore_index=True)
            if matches.duplicated(["competition_group", "match_id"]).any():
                raise ValueError("Published match keys are not unique")
            for name in sorted(members):
                if not name.endswith("/source_points.csv.gz"):
                    continue
                offset, nonempty, selected_count = 0, 0, 0
                for chunk in csv_chunks(tar, members[name], declared[name], {"match_id", "Pt", "Svr", "Notes"}):
                    nonempty += int((chunk.Notes.notna() & chunk.Notes.str.strip().ne("")).sum())
                    selected = mention_rows(chunk, name, declared[name]["sha256"], offset)
                    rows.extend(selected)
                    selected_count += len(selected)
                    offset += len(chunk)
                source_counts.append({"source_member": name, "source_rows_scanned": offset, "nonempty_note_rows": nonempty, "candidate_category_rows": selected_count})
                print(json.dumps(source_counts[-1]), flush=True)
            frame = pd.DataFrame(rows, columns=BASE_COLUMNS)
            selected_matches = set(frame.match_id.dropna())
            for name in sorted(members):
                if name.endswith("/point_states.csv.gz"):
                    group = "mens_singles" if "/mens_singles/" in name else "womens_singles"
                    for chunk in csv_chunks(tar, members[name], declared[name], STATE_COLUMNS):
                        chunk = chunk.loc[chunk.match_id.isin(selected_matches)].copy()
                        chunk["point_join_key"] = chunk.source_point_number.map(point_key)
                        chunk = chunk.loc[chunk.point_join_key.notna() & chunk.match_id.notna()]
                        chunk["competition_group"] = group
                        states_parts.append(chunk.drop(columns="source_point_number"))
            states = pd.concat(states_parts, ignore_index=True) if states_parts else pd.DataFrame(columns=["match_id", "point_join_key", "competition_group", "evaluation_split", "pre_score_prefix_valid"])
            if states.duplicated(["competition_group", "match_id", "point_join_key"]).any():
                raise ValueError("Published canonical point keys are not unique")
            frame = frame.merge(matches, on=["competition_group", "match_id"], how="left", validate="many_to_one")
            frame = frame.merge(states, on=["competition_group", "match_id", "point_join_key"], how="left", validate="many_to_one")
            frame["canonical_point_join_found"] = frame.evaluation_split.notna()
            frame["historical_wallclock_available"] = False
            frame["license"] = LICENSE
            frame = frame.sort_values(["competition_group", "match_date", "match_id", "source_member", "source_row_number", "mention_category"], na_position="last")
            tables = {"candidate_mentions": write(frame, output / "candidate_mentions.csv.gz")}
            counts = frame.groupby(["competition_group", "evaluation_split", "mention_category"], dropna=False).agg(
                candidate_category_rows=("source_record_id", "size"), distinct_source_points=("source_row_number", "size"),
                distinct_matches=("match_id", "nunique")).reset_index()
            # Within one category each source row is emitted once. Original duplicate
            # source points remain separate and are never claimed as separate events.
            counts = counts.rename(columns={"distinct_source_points": "source_note_rows"})
            tables["candidate_counts"] = write(counts, output / "candidate_counts.csv.gz")
            for basename in ["LICENSE.txt", "SOURCE_README.md", "SOURCE_data_dictionary.txt"]:
                name = PREFIX + basename
                if name not in members:
                    raise ValueError("Required source license/dictionary missing")
                content = tar.extractfile(members[name]).read()
                if hashlib.sha256(content).hexdigest() != declared[name]["sha256"]:
                    raise ValueError("Source license/dictionary integrity failed")
                (output / basename).write_bytes(content)
        summary = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "source": provenance, "license": LICENSE,
            "research_only_noncommercial": True, "commercial_betting_eligibility_claimed": False,
            "source_rows_scanned": sum(item["source_rows_scanned"] for item in source_counts),
            "nonempty_note_rows": sum(item["nonempty_note_rows"] for item in source_counts),
            "candidate_category_rows": len(frame), "matches_with_candidates": int(frame.match_id.nunique()),
            "canonical_point_join_found_rows": int(frame.canonical_point_join_found.sum()),
            "candidate_rows_by_category": frame.mention_category.value_counts().to_dict(),
            "candidate_rows_by_split": frame.evaluation_split.fillna("unresolved_canonical_point").value_counts().to_dict(),
            "negation_word_present_rows": int(frame.negation_word_present.sum()),
            "retrospective_word_present_rows": int(frame.retrospective_word_present.sum()),
            "source_members": source_counts, "tables": tables,
            "limitations": ["Keyword matches are unverified candidate mentions, not confirmed events, diagnoses, referee decisions or event counts.",
                "A note can refer to an opponent, an earlier event, a negated event or a general observation. Simple negation flags do not resolve attribution.",
                "No mention does not mean absence. The selected charted sample and optional notes are not representative.",
                "Publication times and event wallclocks are unavailable; notes attached to a point are not automatically valid before that point.",
                "Existing canonical evaluation_split and source match-date precision are carried forward; unmatched source points stay unresolved.",
                "Source duplicate records remain separately traceable; category counts are source mentions, not independent incidents.",
                "Noncommercial research only, attribution and share-alike required. No model training or trades are performed."]}
        (output / "summary.json").write_text(json.dumps(summary, indent=2))
        (output / "schema.json").write_text(json.dumps({"csv_null_encoding": r"\N", "tables": tables,
            "category_rules": RULES, "point_join_key": "Integral original Pt matched to published canonical source_point_number; null when invalid",
            "source_notes": "Licensed original free text retained only for research audit; not an automatic model feature",
            "changes": "Selected keyword mention candidates, source-provenance IDs, existing canonical point/date metadata join and aggregate counts"}, indent=2))
    finally:
        shutil.rmtree(cache, ignore_errors=True)


if __name__ == "__main__":
    main()
