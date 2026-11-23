from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


PACKAGE_NAME = "WSH M1.3 Semantic and Policy Validation"
POLICY_VERSION = "0.1"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
FUTURE_TOLERANCE = timedelta(minutes=5)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finding(code: str, severity: str, message: str, dataset_key: str | None = None) -> dict[str, Any]:
    item = {"code": code, "severity": severity, "message": message}
    if dataset_key is not None:
        item["dataset_key"] = dataset_key
    return item


def evaluate_dataset(
    status: dict[str, Any],
    *,
    contract: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
    evidence_records: list[dict[str, Any]] | None = None,
    evidence_bytes: dict[str, bytes] | None = None,
    market_calendar: dict[str, Any] | None = None,
    now_utc: str | None = None,
) -> dict[str, Any]:
    """Evaluate one M1.1-shaped dataset status record without mutating inputs.

    evidence_bytes maps evidence_id to the exact captured bytes. If omitted,
    digest verification is reported as NOT_EVALUATED, never silently PASS.
    market_calendar must explicitly provide market_open for calendar-dependent
    checks; weekdays are never used as a substitute for an exchange calendar.
    """
    contract = contract or {}
    registry = registry or {}
    run_context = run_context or {}
    evidence_records = evidence_records or []
    evidence_bytes = evidence_bytes or {}
    market_calendar = market_calendar or {}
    findings: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    source_id = status.get("source_id")
    dataset_key = status.get("dataset_key")
    pub = status.get("publication_status")
    ext = status.get("extraction_status")
    val = status.get("validation_status")
    fresh = status.get("freshness_status")
    recon = status.get("reconciliation_status")
    decision = status.get("release_decision")
    limitations = status.get("limitations") or []
    required_evidence = bool((contract.get("evidence") or {}).get("raw_capture_required"))
    reconciliation_required = bool((contract.get("validation") or {}).get("reconciliation_rule"))

    def error(code: str, message: str) -> None:
        findings.append(_finding(code, "ERROR", message, dataset_key))

    def warning(code: str, message: str) -> None:
        warnings.append(_finding(code, "WARNING", message, dataset_key))

    # P1/P2: Cross-field status and release policy.
    if val == "INVALID" and decision != "REJECT":
        error("INVALID_MUST_REJECT", "INVALID validation status requires REJECT.")
    if val == "QUARANTINED" and decision not in ("HOLD", "REJECT"):
        error("QUARANTINED_CANNOT_RELEASE", "QUARANTINED data must be HOLD or REJECT.")
    if val == "NOT_RUN" and decision not in ("HOLD", "REJECT"):
        error("VALIDATION_NOT_RUN_CANNOT_RELEASE", "NOT_RUN validation must be HOLD or REJECT.")
    if ext == "FAILED" and decision not in ("HOLD", "REJECT"):
        error("FAILED_EXTRACTION_CANNOT_RELEASE", "FAILED extraction must be HOLD or REJECT.")
    if ext == "NOT_ATTEMPTED" and decision not in ("HOLD", "REJECT"):
        error("EXTRACTION_NOT_ATTEMPTED_CANNOT_RELEASE", "NOT_ATTEMPTED extraction must be HOLD or REJECT.")
    if ext == "PARTIAL" and decision == "ACCEPT":
        error("PARTIAL_CANNOT_ACCEPT", "PARTIAL extraction cannot receive unconditional ACCEPT.")
    if fresh in ("STALE", "OUT_OF_WINDOW", "UNKNOWN") and decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"):
        error("FRESHNESS_GATE_FAILED", f"{fresh} freshness is not eligible for release.")
    if recon == "MISMATCH" and decision != "REJECT":
        error("RECONCILIATION_MISMATCH_MUST_REJECT", "MISMATCH reconciliation requires REJECT.")
    if recon == "NOT_RUN" and reconciliation_required and decision not in ("HOLD", "REJECT"):
        error("RECONCILIATION_REQUIRED_NOT_RUN", "Required reconciliation has not been performed.")
    if recon == "NOT_AVAILABLE" and reconciliation_required and decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"):
        error("REQUIRED_RECONCILIATION_UNAVAILABLE", "Contract requires reconciliation but status is NOT_AVAILABLE.")
    if decision == "ACCEPT" and not (
        pub == "PUBLISHED" and ext == "SUCCESS" and val == "VALID"
        and fresh == "FRESH" and recon in ("MATCHED", "NOT_AVAILABLE")
    ):
        error("ACCEPT_PREREQUISITES_NOT_MET", "ACCEPT requires PUBLISHED, SUCCESS, VALID, FRESH and an eligible reconciliation state.")
    if decision == "ACCEPT_WITH_LIMITATIONS":
        if not limitations:
            error("LIMITATIONS_NOT_DOCUMENTED", "ACCEPT_WITH_LIMITATIONS requires at least one documented limitation.")
        if val in ("INVALID", "QUARANTINED", "NOT_RUN") or ext in ("FAILED", "NOT_ATTEMPTED") or recon == "MISMATCH":
            error("LIMITED_ACCEPT_FORBIDDEN_STATE", "Current validation, extraction or reconciliation state forbids limited acceptance.")

    # Preserve legitimate closed-market semantics; closed is not an extraction failure.
    if pub == "MARKET_CLOSED" and ext == "NOT_ATTEMPTED" and val == "NOT_RUN":
        if decision not in ("HOLD", "REJECT"):
            error("CLOSED_MARKET_NOT_ATTEMPTED_REQUIRES_HOLD", "MARKET_CLOSED + NOT_ATTEMPTED + NOT_RUN must not be released.")
        if market_calendar.get("market_open") is True:
            error("CALENDAR_STATUS_CONTRADICTION", "Status says MARKET_CLOSED but supplied calendar says market_open=true.")
    elif pub == "MARKET_CLOSED" and "market_open" not in market_calendar:
        warning("CALENDAR_NOT_SUPPLIED", "Market calendar was not supplied; calendar-dependent status was not independently confirmed.")

    # P3: Publication is independent from extraction/validation. Do not infer success.
    if pub in ("NOT_DUE", "DELAYED", "WITHDRAWN", "UNKNOWN") and decision == "ACCEPT":
        error("PUBLICATION_STATE_NOT_ACCEPTABLE", f"{pub} publication state cannot receive ACCEPT.")
    if pub == "PUBLISHED" and ext != "SUCCESS" and decision == "ACCEPT":
        error("PUBLISHED_EXTRACTION_INCONSISTENT", "A published source record with unsuccessful extraction cannot be accepted.")

    # P4: Source and dataset identity isolation.
    identity_objects = [("contract", contract), ("registry", registry)]
    for label, obj in identity_objects:
        if obj:
            if obj.get("source_id") != source_id:
                error("SOURCE_IDENTITY_MISMATCH", f"{label} source_id does not match status source_id.")
            if obj.get("dataset_key") != dataset_key:
                error("DATASET_KEY_MISMATCH", f"{label} dataset_key does not match status dataset_key.")
    if registry and dataset_key not in (registry.get("dataset_key"),):
        error("REGISTRY_DATASET_KEY_MISMATCH", "Dataset key is not the registered key.")
    if contract and source_id and contract.get("source_id") != source_id:
        error("CONTRACT_SOURCE_MISMATCH", "Dataset contract belongs to another source.")

    # P5: Evidence linkage, provenance and digest.
    matching_evidence = []
    for ev in evidence_records:
        if ev.get("run_id") == status.get("run_id") and ev.get("source_id") == source_id and ev.get("dataset_key") == dataset_key:
            matching_evidence.append(ev)
        elif ev.get("run_id") == status.get("run_id"):
            error("EVIDENCE_IDENTITY_MISMATCH", "Evidence attached to this run has a different source_id or dataset_key.")
    if required_evidence and not matching_evidence:
        error("REQUIRED_EVIDENCE_MISSING", "Contract requires raw evidence but no matching evidence record is attached.")

    hash_states = []
    current = _parse_dt(now_utc) if now_utc else datetime.now(timezone.utc)
    run_started = _parse_dt(run_context.get("run_started_at_utc"))
    for ev in matching_evidence:
        digest = ev.get("sha256", "")
        if not SHA256_RE.fullmatch(digest):
            error("EVIDENCE_HASH_FORMAT_INVALID", "Evidence SHA-256 must be 64 lowercase hexadecimal characters.")
            hash_states.append("FAIL")
        else:
            raw = evidence_bytes.get(ev.get("evidence_id"))
            if raw is None:
                hash_states.append("NOT_EVALUATED")
                warning("HASH_BYTES_NOT_SUPPLIED", "Hash format is valid, but actual digest verification was not possible because evidence bytes were not supplied.")
            elif hashlib.sha256(raw).hexdigest() != digest:
                error("EVIDENCE_HASH_MISMATCH", "Computed SHA-256 does not match the evidence record.")
                hash_states.append("FAIL")
            else:
                hash_states.append("PASS")
        captured = _parse_dt(ev.get("captured_at_utc"))
        if captured is None:
            error("EVIDENCE_TIMESTAMP_INVALID", "Evidence captured_at_utc must be a valid timezone-aware timestamp.")
        else:
            if captured > current + FUTURE_TOLERANCE:
                error("EVIDENCE_TIMESTAMP_IN_FUTURE", "Evidence capture timestamp exceeds the five-minute future tolerance.")
            if run_started and captured < run_started - timedelta(days=1):
                warning("EVIDENCE_PRECEDES_RUN", "Evidence capture predates the run by more than one day; verify replay/backfill provenance.")
        if run_context and ev.get("run_id") != run_context.get("run_id"):
            error("EVIDENCE_RUN_ID_MISMATCH", "Evidence run_id does not match run context.")

    if required_evidence and matching_evidence and decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS") and (
        not hash_states or any(state != "PASS" for state in hash_states)
    ):
        error("REQUIRED_EVIDENCE_HASH_UNVERIFIED", "Evidence is required for release, but its digest has not been verified against captured bytes.")

    # P6: Freshness and calendar evidence. No weekday-only inference.
    if fresh in ("FRESH", "STALE", "OUT_OF_WINDOW") and not run_context.get("market_date"):
        warning("MARKET_DATE_MISSING", "Run market_date is absent; freshness label cannot be cross-checked against run context.")
    if market_calendar and "market_open" not in market_calendar:
        error("CALENDAR_INPUT_INCOMPLETE", "Calendar input must explicitly contain market_open.")
    if market_calendar.get("market_open") is False and pub == "PUBLISHED":
        warning("PUBLISHED_ON_CLOSED_SESSION", "Source reports publication on a closed session; verify special-session or delayed-publication semantics.")

    # P7: Reconciliation.
    if recon == "MISMATCH" and decision != "REJECT":
        error("RECONCILIATION_MISMATCH_MUST_REJECT", "Reconciliation mismatch blocks release and requires REJECT.")
    if reconciliation_required and recon in ("NOT_RUN", "NOT_AVAILABLE"):
        warning("RECONCILIATION_INCOMPLETE", "Contract requires reconciliation but it is not complete.")

    if decision not in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS", "HOLD", "REJECT"):
        error("UNKNOWN_RELEASE_DECISION", "Release decision is not a recognized M1.1 value.")

    integrity = {
        "source_identity": "FAIL" if any(x["code"] in ("SOURCE_IDENTITY_MISMATCH", "DATASET_KEY_MISMATCH", "REGISTRY_DATASET_KEY_MISMATCH", "CONTRACT_SOURCE_MISMATCH", "EVIDENCE_IDENTITY_MISMATCH") for x in findings) else "PASS",
        "evidence_provenance": "FAIL" if any(x["code"].startswith("EVIDENCE_") or x["code"] == "REQUIRED_EVIDENCE_MISSING" for x in findings) else ("PASS" if matching_evidence else "NOT_EVALUATED"),
        "hash_verification": "FAIL" if "FAIL" in hash_states else ("PASS" if hash_states and all(x == "PASS" for x in hash_states) else "NOT_EVALUATED"),
        "calendar_validation": "FAIL" if any(x["code"].startswith("CALENDAR_") for x in findings) else ("PASS" if "market_open" in market_calendar else "NOT_EVALUATED")
    }
    result = {
        "dataset_key": dataset_key,
        "source_id": source_id,
        "declared_release_decision": decision,
        "policy_result": "FAIL" if findings else ("PASS_WITH_WARNINGS" if warnings else "PASS"),
        "release_eligibility": "REJECT" if any(x["severity"] == "ERROR" and x["code"] in ("INVALID_MUST_REJECT", "RECONCILIATION_MISMATCH_MUST_REJECT", "RECONCILIATION_MISMATCH") for x in findings) else (decision if not findings else "HOLD"),
        "integrity": integrity,
        "findings": findings,
        "warnings": warnings
    }
    return result


def build_report(results: list[dict[str, Any]], generated_at_utc: str | None = None) -> dict[str, Any]:
    generated = generated_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    findings = []
    for result in results:
        findings.extend(result.get("findings", []))
        findings.extend(result.get("warnings", []))
    failed = sum(1 for r in results if r["policy_result"] == "FAIL")
    passed = len(results) - failed
    def aggregate(key: str) -> str:
        values = [r["integrity"][key] for r in results]
        if "FAIL" in values:
            return "FAIL"
        if not values or all(v == "NOT_EVALUATED" for v in values):
            return "NOT_EVALUATED"
        return "PASS" if all(v == "PASS" for v in values) else "NOT_EVALUATED"
    eligibility = "NOT_EVALUATED"
    if results:
        decisions = [r["release_eligibility"] for r in results]
        if "REJECT" in decisions:
            eligibility = "REJECT"
        elif "HOLD" in decisions or "NOT_EVALUATED" in decisions:
            eligibility = "HOLD"
        elif "ACCEPT_WITH_LIMITATIONS" in decisions:
            eligibility = "ACCEPT_WITH_LIMITATIONS"
        elif all(d == "ACCEPT" for d in decisions):
            eligibility = "ACCEPT"
        else:
            eligibility = "HOLD"
    return {
        "package": PACKAGE_NAME,
        "policy_version": POLICY_VERSION,
        "generated_at_utc": generated,
        "execution": {
            "result": "PASS" if failed == 0 else "FAIL",
            "policies_evaluated": len(results),
            "passed": passed,
            "failed": failed,
            "warnings": sum(len(r.get("warnings", [])) for r in results)
        },
        "integrity": {
            "source_identity": aggregate("source_identity"),
            "evidence_provenance": aggregate("evidence_provenance"),
            "hash_verification": aggregate("hash_verification"),
            "calendar_validation": aggregate("calendar_validation")
        },
        "release_eligibility": eligibility,
        "findings": findings
    }


def write_report(report: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
