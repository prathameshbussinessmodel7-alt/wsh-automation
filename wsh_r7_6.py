"""
WSH R7.6.1 — append-only historical evidence archive.

This module is deliberately independent of Selenium and the live collector.
It ingests an existing collector output directory after collection/validation.
It preserves raw evidence and metadata, records provenance in SQLite, and never
uses filesystem modification time as a source or market date.

Usage:
  python wsh_r7_6.py --input-dir WSH_FII_CCIL_RND --archive-dir WSH_HISTORY
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

VERSION = "WSH_R7.6.1"
DATASETS = {
    "fii_dii_cash_activity": "NSE_FII_DII_CASH",
    "ccil_money_market": "CCIL_MONEY_MARKET",
    "ccil_gsec": "CCIL_GSEC",
    "ccil_forex": "CCIL_FOREX",
    "ccil_derivatives": "CCIL_DERIVATIVES",
}

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    pipeline_version TEXT NOT NULL,
    manifest_name TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL UNIQUE,
    execution_timestamp TEXT,
    ingested_at_utc TEXT NOT NULL,
    source_manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payloads (
    sha256 TEXT PRIMARY KEY,
    byte_length INTEGER NOT NULL,
    archive_relative_path TEXT NOT NULL UNIQUE,
    first_seen_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    dataset_key TEXT NOT NULL,
    dataset_code TEXT NOT NULL,
    source TEXT,
    publication_status TEXT,
    extraction_status TEXT,
    validation_status TEXT,
    records_extracted INTEGER NOT NULL,
    source_as_of_date TEXT,
    source_date_status TEXT NOT NULL,
    captured_at TEXT,
    capture_timestamp_source TEXT,
    raw_sha256 TEXT,
    metadata_sha256 TEXT,
    raw_archive_path TEXT,
    metadata_archive_path TEXT,
    raw_payload_status TEXT NOT NULL,
    metadata_payload_status TEXT NOT NULL,
    evidence_metadata_json TEXT NOT NULL,
    UNIQUE(run_id, dataset_key)
);
CREATE INDEX IF NOT EXISTS idx_observations_dataset_date
    ON dataset_observations(dataset_key, source_as_of_date);
CREATE INDEX IF NOT EXISTS idx_observations_capture
    ON dataset_observations(captured_at);
"""

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def parse_aware_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()

def parse_explicit_date(value: Any) -> tuple[str | None, str]:
    """Accept only explicit date values; never substitute capture/file dates."""
    if not isinstance(value, str) or not value.strip():
        return None, "UNKNOWN"
    candidate = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(candidate, fmt).date().isoformat(), "PASS"
        except ValueError:
            continue
    return None, "UNKNOWN"

def source_date_for(dataset_key: str, metadata: dict) -> tuple[str | None, str]:
    if dataset_key == "fii_dii_cash_activity":
        records = metadata.get("records")
        if isinstance(records, list) and records:
            dates = []
            for row in records:
                if isinstance(row, dict):
                    parsed, status = parse_explicit_date(row.get("date"))
                    if status != "PASS":
                        return None, "UNKNOWN"
                    dates.append(parsed)
            unique = sorted(set(dates))
            return (unique[0], "PASS") if len(unique) == 1 else (None, "CONFLICT")
        return None, "UNKNOWN"
    for key in ("publication_date", "published_date", "published_at",
                "source_as_of_date", "as_of_date", "market_date", "report_date"):
        if metadata.get(key) not in (None, ""):
            return parse_explicit_date(str(metadata[key]))
    # Collector's explicit CCIL date extraction is retained as provenance,
    # but UNKNOWN_NOT_EXPLICIT_IN_CAPTURE is deliberately not parsed as a date.
    evidence = metadata.get("source_date_evidence")
    if isinstance(evidence, dict):
        return parse_explicit_date(evidence.get("normalized_date"))
    return None, "UNKNOWN"

def safe_relative_file(root: Path, value: Any) -> Path | None:
    """Resolve collector paths safely, including REPORT_DIR-prefixed paths.

    The collector currently records paths such as
    WSH_FII_CCIL_RND/evidence/file.raw even when --input-dir points directly
    at WSH_FII_CCIL_RND. Accept that known layout without allowing path escape.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value)
    candidates = [candidate.resolve() if candidate.is_absolute() else (Path.cwd() / candidate).resolve()]
    if not candidate.is_absolute():
        candidates.append((root / candidate).resolve())
        parts = candidate.parts
        if parts and parts[0] == root.name:
            candidates.append((root.joinpath(*parts[1:])).resolve())
    for resolved in candidates:
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None

def atomic_copy_immutable(source: Path, destination: Path, expected_hash: str) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(destination) != expected_hash:
            raise RuntimeError(f"Immutable archive collision: {destination}")
        return "ALREADY_PRESENT"
    temp = destination.with_name(destination.name + ".tmp")
    shutil.copyfile(source, temp)
    if sha256_file(temp) != expected_hash:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"Hash mismatch while copying {source}")
    os.replace(temp, destination)
    return "COPIED"

def initialise_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()

def ingest_directory(input_dir: str | Path, archive_dir: str | Path) -> dict:
    root = Path(input_dir).resolve()
    archive = Path(archive_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Collector output directory not found: {root}")
    manifests = sorted(root.glob("WSH_Quality_Manifest_*.json"))
    if not manifests:
        raise FileNotFoundError("No WSH_Quality_Manifest_*.json found")
    db_path = archive / "wsh_history.sqlite3"
    initialise_database(db_path)
    results = []
    for manifest_path in manifests:
        manifest_bytes = manifest_path.read_bytes()
        manifest_hash = sha256_bytes(manifest_bytes)
        manifest = json.loads(manifest_bytes)
        run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{manifest_hash[:12]}"
        ingested_at = datetime.now(timezone.utc).isoformat()
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            existing = connection.execute(
                "SELECT run_id FROM runs WHERE manifest_sha256=?", (manifest_hash,)
            ).fetchone()
            if existing:
                results.append({"manifest": manifest_path.name, "status": "ALREADY_INGESTED",
                                "run_id": existing[0], "observations": 0})
                continue
            # Validate every evidence path and hash before committing this run.
            pending = []
            for dataset_key, dataset_code in DATASETS.items():
                item = manifest.get("datasets", {}).get(dataset_key, {})
                evidence = item.get("evidence") or {}
                raw_path = safe_relative_file(root, evidence.get("raw"))
                meta_path = safe_relative_file(root, evidence.get("metadata"))
                metadata = {}
                if meta_path:
                    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                raw_hash = sha256_file(raw_path) if raw_path else None
                metadata_hash = sha256_file(meta_path) if meta_path else None
                expected_hash = evidence.get("sha256")
                raw_integrity = ("MISSING" if not raw_path else
                                 "PASS" if not expected_hash or raw_hash == expected_hash else "FAIL")
                if raw_integrity == "FAIL":
                    raise RuntimeError(f"Raw evidence hash mismatch: {dataset_key}")
                source_date, date_status = source_date_for(dataset_key, metadata)
                capture = parse_aware_timestamp(metadata.get("captured_at"))
                pending.append({
                    "dataset_key": dataset_key, "dataset_code": dataset_code, "item": item,
                    "metadata": metadata, "raw_path": raw_path, "meta_path": meta_path,
                    "raw_hash": raw_hash, "meta_hash": metadata_hash,
                    "raw_integrity": raw_integrity, "source_date": source_date,
                    "date_status": date_status, "captured_at": capture,
                })
            connection.execute(
                """INSERT INTO runs(run_id,pipeline_version,manifest_name,manifest_sha256,
                   execution_timestamp,ingested_at_utc,source_manifest_json)
                   VALUES(?,?,?,?,?,?,?)""",
                (run_id, str(manifest.get("pipeline_version", "UNKNOWN")),
                 manifest_path.name, manifest_hash, manifest.get("execution_timestamp"),
                 ingested_at, manifest_bytes.decode("utf-8"))
            )
            observation_count = 0
            for row in pending:
                dataset_dir = archive / "evidence" / run_id / row["dataset_code"]
                raw_archive = None
                meta_archive = None
                if row["raw_path"] and row["raw_hash"]:
                    raw_archive = dataset_dir / ("raw_" + row["raw_hash"][:16] + row["raw_path"].suffix)
                    atomic_copy_immutable(row["raw_path"], raw_archive, row["raw_hash"])
                    connection.execute(
                        """INSERT OR IGNORE INTO payloads(sha256,byte_length,archive_relative_path,first_seen_at_utc)
                           VALUES(?,?,?,?)""",
                        (row["raw_hash"], row["raw_path"].stat().st_size,
                         str(raw_archive.relative_to(archive)), ingested_at)
                    )
                if row["meta_path"] and row["meta_hash"]:
                    meta_archive = dataset_dir / ("metadata_" + row["meta_hash"][:16] + ".json")
                    atomic_copy_immutable(row["meta_path"], meta_archive, row["meta_hash"])
                item = row["item"]
                connection.execute(
                    """INSERT INTO dataset_observations(
                       run_id,dataset_key,dataset_code,source,publication_status,extraction_status,
                       validation_status,records_extracted,source_as_of_date,source_date_status,
                       captured_at,capture_timestamp_source,raw_sha256,metadata_sha256,
                       raw_archive_path,metadata_archive_path,raw_payload_status,metadata_payload_status,
                       evidence_metadata_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, row["dataset_key"], row["dataset_code"], item.get("source"),
                     item.get("publication_status"), item.get("extraction_status"),
                     item.get("validation_status"), int(item.get("records_extracted", 0) or 0),
                     row["source_date"], row["date_status"], row["captured_at"],
                     row["metadata"].get("capture_timestamp_source"), row["raw_hash"], row["meta_hash"],
                     str(raw_archive.relative_to(archive)) if raw_archive else None,
                     str(meta_archive.relative_to(archive)) if meta_archive else None,
                     row["raw_integrity"], "PASS" if row["meta_path"] else "MISSING",
                     json.dumps({"failure_reasons": item.get("failure_reasons", []),
                                 "evidence": item.get("evidence", {}),
                                 "source_date_evidence": row["metadata"].get("source_date_evidence")},
                                ensure_ascii=False, sort_keys=True))
                )
                observation_count += 1
            connection.commit()
        finally:
            connection.close()
        results.append({"manifest": manifest_path.name, "status": "INGESTED",
                        "run_id": run_id, "observations": observation_count})
    return {"archive_version": VERSION, "database": str(db_path),
            "manifests_seen": len(manifests), "results": results}

def audit_archive(archive_dir: str | Path) -> dict:
    archive = Path(archive_dir).resolve()
    db_path = archive / "wsh_history.sqlite3"
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        runs = connection.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
        rows = connection.execute(
            """SELECT dataset_key, COUNT(*) AS observations,
                      SUM(CASE WHEN source_as_of_date IS NULL THEN 1 ELSE 0 END) AS unknown_source_dates,
                      COUNT(DISTINCT source_as_of_date) AS distinct_source_dates
               FROM dataset_observations GROUP BY dataset_key ORDER BY dataset_key"""
        ).fetchall()
        integrity = connection.execute(
            "SELECT COUNT(*) AS n FROM dataset_observations WHERE raw_payload_status='FAIL'"
        ).fetchone()["n"]
    finally:
        connection.close()
    return {"archive_version": VERSION, "run_count": runs,
            "dataset_history": [dict(row) for row in rows],
            "raw_integrity_failures": integrity,
            "continuity_status": "NOT_ASSESSED_IN_R7.6.1",
            "note": "Unknown source dates remain unknown; calendar gaps are not inferred in the archive stage."}

def main() -> None:
    parser = argparse.ArgumentParser(description="WSH R7.6.1 historical evidence archive")
    parser.add_argument("--input-dir", default="WSH_FII_CCIL_RND")
    parser.add_argument("--archive-dir", default="WSH_HISTORY")
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    result = audit_archive(args.archive_dir) if args.audit_only else ingest_directory(args.input_dir, args.archive_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
