from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

PACKAGE_NAME = "WSH M1.3 Semantic and Policy Validation"
POLICY_VERSION = "0.1.4"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
def _load_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = path or (Path(__file__).resolve().parent / "policy_rules.json")
    try:
        with policy_path.open("r", encoding="utf-8-sig") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load WSH policy configuration: {exc}") from exc
    if not isinstance(config, dict):
        raise RuntimeError("WSH policy configuration must be a JSON object.")
    if config.get("package") != PACKAGE_NAME:
        raise RuntimeError("WSH policy configuration package must match PACKAGE_NAME.")
    if config.get("policy_version") != POLICY_VERSION:
        raise RuntimeError("WSH policy configuration version must match policy_engine.POLICY_VERSION.")
    freshness = config.get("freshness_policy")
    evidence = config.get("evidence_policy")
    if not isinstance(freshness, dict) or not isinstance(evidence, dict):
        raise RuntimeError("WSH policy configuration requires freshness_policy and evidence_policy objects.")
    for section, key in (
        (freshness, "future_timestamp_tolerance_seconds"),
        (evidence, "future_capture_tolerance_seconds"),
    ):
        value = section.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError(f"Policy tolerance {key} must be a non-negative integer.")
    return config


POLICY_RULES = _load_policy()

ENUMS = {
    "publication_status": {"PUBLISHED", "MARKET_CLOSED", "NOT_DUE", "DELAYED", "WITHDRAWN", "UNKNOWN"},
    "extraction_status": {"SUCCESS", "PARTIAL", "FAILED", "NOT_ATTEMPTED", "NOT_RUN", "UNKNOWN"},
    "validation_status": {"VALID", "INVALID", "QUARANTINED", "NOT_RUN", "UNKNOWN"},
    "freshness_status": {"FRESH", "STALE", "OUT_OF_WINDOW", "UNKNOWN", "NOT_EVALUATED"},
    "reconciliation_status": {"MATCHED", "MISMATCH", "NOT_AVAILABLE", "NOT_RUN", "UNKNOWN"},
    "release_decision": {"ACCEPT", "ACCEPT_WITH_LIMITATIONS", "HOLD", "REJECT"},
}
INTEGRITY_KEYS = ("source_identity", "evidence_provenance", "hash_verification", "freshness_verification", "calendar_validation")


def _parse_dt(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
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
    status: dict[str, Any], *, contract: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None, run_context: dict[str, Any] | None = None,
    evidence_records: list[dict[str, Any]] | None = None,
    evidence_bytes: dict[str, bytes] | None = None,
    market_calendar: dict[str, Any] | None = None, now_utc: str | None = None,
) -> dict[str, Any]:
    """Evaluate a single M1.1-shaped record. Inputs are never mutated.

    ACCEPT requires independently verified identity and freshness. Contract-required
    evidence/calendar checks must be supplied and pass. NOT_REQUIRED is distinct from
    NOT_EVALUATED; the latter cannot satisfy a required release gate.
    """
    findings: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    source_id = status.get("source_id")
    dataset_key = status.get("dataset_key")

    def error(code: str, message: str) -> None:
        findings.append(_finding(code, "ERROR", message, dataset_key))

    def warning(code: str, message: str) -> None:
        warnings.append(_finding(code, "WARNING", message, dataset_key))

    def mapping_or_empty(value: Any, name: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            error("INPUT_OBJECT_INVALID", f"{name} must be an object when supplied.")
            return {}
        return value

    contract = mapping_or_empty(contract, "contract")
    registry = mapping_or_empty(registry, "registry")
    run_context = mapping_or_empty(run_context, "run_context")

    if evidence_records is None:
        evidence_records = []
    elif not isinstance(evidence_records, list):
        error("EVIDENCE_RECORDS_INPUT_INVALID", "evidence_records must be a list when supplied.")
        evidence_records = []

    if evidence_bytes is None:
        evidence_bytes = {}
    elif not isinstance(evidence_bytes, dict):
        error("EVIDENCE_BYTES_INPUT_INVALID", "evidence_bytes must be an object when supplied.")
        evidence_bytes = {}

    calendar_supplied = market_calendar is not None
    if market_calendar is None:
        market_calendar = {}

    decision = status.get("release_decision")
    pub, ext, val, fresh, recon = (status.get(k) for k in (
        "publication_status", "extraction_status", "validation_status", "freshness_status", "reconciliation_status"))
    limitations = status.get("limitations") or []

    evidence_contract = contract.get("evidence")
    if evidence_contract is None:
        evidence_contract = {}
    elif not isinstance(evidence_contract, dict):
        error("EVIDENCE_CONTRACT_INVALID", "contract.evidence must be an object.")
        evidence_contract = {}
    raw_capture_required = evidence_contract.get("raw_capture_required", False)
    if not isinstance(raw_capture_required, bool):
        error("RAW_CAPTURE_REQUIRED_INVALID", "contract.evidence.raw_capture_required must be Boolean.")
        required_evidence = False
    else:
        required_evidence = raw_capture_required

    validation_contract = contract.get("validation")
    if validation_contract is None:
        validation_contract = {}
    elif not isinstance(validation_contract, dict):
        error("VALIDATION_CONTRACT_INVALID", "contract.validation must be an object.")
        validation_contract = {}

    reconciliation_rule = validation_contract.get("reconciliation_rule")
    if reconciliation_rule is not None and (
        not isinstance(reconciliation_rule, str) or not reconciliation_rule.strip()
    ):
        error("RECONCILIATION_RULE_INVALID", "reconciliation_rule must be null or a non-empty string.")
        reconciliation_required = False
    else:
        reconciliation_required = isinstance(reconciliation_rule, str)

    calendar_required_value = validation_contract.get("calendar_required", False)
    if not isinstance(calendar_required_value, bool):
        error("CALENDAR_REQUIRED_INVALID", "calendar_required must be Boolean.")
        calendar_required_value = False

    calendar_rule = validation_contract.get("calendar_rule")
    if calendar_rule is not None and (
        not isinstance(calendar_rule, str) or not calendar_rule.strip()
    ):
        error("CALENDAR_RULE_INVALID", "calendar_rule must be null or a non-empty string.")
        calendar_rule = None
    calendar_required = calendar_required_value or isinstance(calendar_rule, str)

    freshness_policy = contract.get("freshness")
    if freshness_policy is None:
        freshness_policy = {}
    elif not isinstance(freshness_policy, dict):
        error("FRESHNESS_POLICY_INVALID", "contract.freshness must be an object.")
        freshness_policy = {}
    max_age = freshness_policy.get("max_age_seconds")

    if now_utc is None:
        current_time = datetime.now(timezone.utc)
    else:
        current_time = _parse_dt(now_utc)
        if current_time is None:
            error("NOW_TIMESTAMP_INVALID", "now_utc must be a valid timezone-aware timestamp when supplied.")

    # Required status dimensions: missing and unknown values are never implicitly valid.
    for field, allowed in ENUMS.items():
        value = status.get(field)
        if not isinstance(value, str) or value not in allowed:
            error("STATUS_DIMENSION_INVALID", f"{field} is missing or has an unsupported value: {value!r}.")
    if not isinstance(source_id, str) or not source_id.strip():
        error("SOURCE_ID_MISSING", "source_id is required and must be a non-empty string.")
    if not isinstance(dataset_key, str) or not dataset_key.strip():
        error("DATASET_KEY_MISSING", "dataset_key is required and must be a non-empty string.")
    if not isinstance(decision, str) or decision not in ENUMS["release_decision"]:
        error("UNKNOWN_RELEASE_DECISION", "Release decision is missing or not a recognized M1.1 value.")

    # A release must be tied to the exact execution run that supplied its context.
    if decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"):
        status_run_id = status.get("run_id")
        context_run_id = run_context.get("run_id")
        if not isinstance(status_run_id, str) or not status_run_id.strip():
            error("STATUS_RUN_ID_MISSING", "Release requires a non-empty status.run_id.")
        elif not isinstance(context_run_id, str) or not context_run_id.strip():
            error("RUN_CONTEXT_ID_MISSING", "Release requires a non-empty run_context.run_id.")
        elif status_run_id != context_run_id:
            error("RUN_CONTEXT_IDENTITY_MISMATCH", "status.run_id must exactly match run_context.run_id.")

    # Identity is PASS only after at least one independent reference is supplied and matched.
    identity_refs = [(name, obj) for name, obj in (("contract", contract), ("registry", registry))
                   if obj and ("source_id" in obj or "dataset_key" in obj)]
    identity_failed = False
    for name, obj in identity_refs:
        if obj.get("source_id") != source_id or obj.get("dataset_key") != dataset_key:
            identity_failed = True
            error("SOURCE_IDENTITY_MISMATCH" if obj.get("source_id") != source_id else "DATASET_KEY_MISMATCH",
                  f"{name} source_id and dataset_key must both match the status record.")
    source_identity = "FAIL" if identity_failed else ("PASS" if identity_refs else "NOT_EVALUATED")
    if decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS") and source_identity != "PASS":
        error("IDENTITY_NOT_VERIFIED", "Release requires independently verified source and dataset identity.")

    # Release-state rules.
    if val == "INVALID" and decision != "REJECT":
        error("INVALID_MUST_REJECT", "INVALID validation status requires REJECT.")
    if recon == "MISMATCH" and decision != "REJECT":
        error("RECONCILIATION_MISMATCH_MUST_REJECT", "MISMATCH reconciliation requires REJECT.")
    if val == "QUARANTINED" and decision not in ("HOLD", "REJECT"):
        error("QUARANTINED_CANNOT_RELEASE", "QUARANTINED data must be HOLD or REJECT.")
    if val == "NOT_RUN" and decision not in ("HOLD", "REJECT"):
        error("VALIDATION_NOT_RUN_CANNOT_RELEASE", "NOT_RUN validation must be HOLD or REJECT.")
    if ext in ("FAILED", "NOT_ATTEMPTED", "NOT_RUN") and decision not in ("HOLD", "REJECT"):
        error("EXTRACTION_NOT_SUCCESSFUL_CANNOT_RELEASE", f"{ext} extraction must be HOLD or REJECT.")
    if fresh in ("STALE", "OUT_OF_WINDOW", "UNKNOWN", "NOT_EVALUATED") and decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"):
        error("FRESHNESS_GATE_FAILED", f"{fresh} freshness is not eligible for release.")
    if pub != "PUBLISHED" and decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"):
        error("PUBLICATION_STATE_NOT_ACCEPTABLE", f"{pub} publication state cannot be released.")
    if decision == "ACCEPT" and ext != "SUCCESS":
        error("ACCEPT_REQUIRES_SUCCESSFUL_EXTRACTION", "Unconditional ACCEPT requires SUCCESS extraction.")
    if decision == "ACCEPT" and val != "VALID":
        error("ACCEPT_REQUIRES_VALIDATION", "Unconditional ACCEPT requires VALID validation.")
    if decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS") and recon not in ("MATCHED", "NOT_AVAILABLE"):
        error("RECONCILIATION_STATE_NOT_ELIGIBLE", "Release requires MATCHED reconciliation or NOT_AVAILABLE when reconciliation is not contractually required.")
    if decision == "ACCEPT_WITH_LIMITATIONS":
        if not isinstance(limitations, list) or not limitations or not all(isinstance(x, str) and x.strip() for x in limitations):
            error("LIMITATIONS_NOT_DOCUMENTED", "ACCEPT_WITH_LIMITATIONS requires non-empty documented limitations.")
        if ext not in ("SUCCESS", "PARTIAL") or val != "VALID":
            error("LIMITED_ACCEPT_STATE_FORBIDDEN", "Limited acceptance requires SUCCESS/PARTIAL extraction and VALID validation.")

    # Freshness must be independently verifiable, not merely declared FRESH.
    freshness_verification = "NOT_EVALUATED"
    max_age_valid = False
    if isinstance(max_age, (int, float)) and not isinstance(max_age, bool) and max_age > 0:
        try:
            max_age_valid = math.isfinite(max_age)
        except OverflowError:
            max_age_valid = False
    if max_age is not None and not max_age_valid:
        error("FRESHNESS_POLICY_INVALID", "Contract freshness.max_age_seconds must be a finite positive number.")
    if max_age_valid:
        updated = _parse_dt(status.get("updated_at_utc"))
        current = current_time
        market_date = run_context.get("market_date")
        market_date_valid = False
        if not isinstance(market_date, str) or not market_date.strip():
            error("MARKET_DATE_MISSING", "run_context.market_date is required for freshness verification.")
        else:
            try:
                parsed_market_date = date.fromisoformat(market_date)
                market_date_valid = parsed_market_date.isoformat() == market_date
            except ValueError:
                market_date_valid = False
            if not market_date_valid:
                error("MARKET_DATE_INVALID", "run_context.market_date must be a real date in canonical YYYY-MM-DD format.")
        if updated is None:
            error("UPDATED_TIMESTAMP_INVALID", "updated_at_utc must be a timezone-aware timestamp for freshness verification.")
        elif current is None:
            freshness_verification = "FAIL"
        else:
            age = (current - updated).total_seconds()
            if age < -POLICY_RULES["freshness_policy"]["future_timestamp_tolerance_seconds"]:
                error("UPDATED_TIMESTAMP_IN_FUTURE", "updated_at_utc exceeds the configured future timestamp tolerance.")
                freshness_verification = "FAIL"
            elif age > max_age:
                error("FRESHNESS_AGE_EXCEEDED", f"Dataset age {int(age)} seconds exceeds contract limit {int(max_age)} seconds.")
                freshness_verification = "FAIL"
            elif fresh != "FRESH":
                freshness_verification = "FAIL"
            elif market_date_valid:
                freshness_verification = "PASS"
    else:
        warning("FRESHNESS_POLICY_NOT_SUPPLIED", "No positive contract freshness.max_age_seconds supplied; independent freshness verification was not evaluated.")
    if decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS") and freshness_verification != "PASS":
        error("FRESHNESS_NOT_VERIFIED", "Release requires independently verified freshness using a contract age limit and market date.")

    # Evidence provenance and hash verification.
    matching_evidence = []
    for ev in evidence_records:
        if not isinstance(ev, dict):
            error("EVIDENCE_RECORD_INVALID", "Each evidence_records entry must be an object.")
            continue
        if ev.get("run_id") == status.get("run_id") and ev.get("source_id") == source_id and ev.get("dataset_key") == dataset_key:
            matching_evidence.append(ev)
        elif ev.get("run_id") == status.get("run_id"):
            error("EVIDENCE_IDENTITY_MISMATCH", "Evidence attached to this run has a different source_id or dataset_key.")
    evidence_provenance = "NOT_REQUIRED" if not required_evidence else "NOT_EVALUATED"
    hash_verification = "NOT_REQUIRED" if not required_evidence else "NOT_EVALUATED"
    hash_states: list[str] = []
    if required_evidence:
        if not matching_evidence:
            error("REQUIRED_EVIDENCE_MISSING", "Contract requires raw evidence but no matching evidence record is attached.")
            evidence_provenance = "FAIL"
            hash_verification = "FAIL"
        else:
            provenance_ok = True
            current = current_time
            for ev in matching_evidence:
                evidence_id = ev.get("evidence_id")
                captured = _parse_dt(ev.get("captured_at_utc"))
                evidence_id_valid = isinstance(evidence_id, str) and bool(evidence_id.strip())
                if not evidence_id_valid or not captured:
                    provenance_ok = False
                    error("EVIDENCE_PROVENANCE_INVALID", "Evidence requires a non-empty string evidence_id and a valid timezone-aware captured_at_utc.")
                if current is None:
                    provenance_ok = False
                    error("EVIDENCE_TIME_REFERENCE_UNAVAILABLE", "Evidence capture time cannot be checked because now_utc was invalid.")
                elif captured is not None and captured > current + timedelta(seconds=POLICY_RULES["evidence_policy"]["future_capture_tolerance_seconds"]):
                    provenance_ok = False
                    error("EVIDENCE_TIMESTAMP_IN_FUTURE", "Evidence capture timestamp exceeds the configured future capture tolerance.")
                if ev.get("run_id") != run_context.get("run_id"):
                    provenance_ok = False
                    error("EVIDENCE_RUN_ID_MISMATCH", "Evidence run_id does not match run context.")
                digest = ev.get("sha256", "")
                if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                    hash_states.append("FAIL")
                    error("EVIDENCE_HASH_FORMAT_INVALID", "Evidence SHA-256 must be 64 lowercase hexadecimal characters.")
                elif not evidence_id_valid:
                    hash_states.append("FAIL")
                    error("EVIDENCE_ID_INVALID", "Evidence evidence_id must be a non-empty string.")
                else:
                    raw = evidence_bytes.get(evidence_id)
                    if raw is None:
                        hash_states.append("NOT_EVALUATED")
                    elif not isinstance(raw, (bytes, bytearray, memoryview)):
                        hash_states.append("FAIL")
                        error("EVIDENCE_BYTES_INPUT_INVALID", "Evidence payloads must be bytes-like values.")
                    elif hashlib.sha256(bytes(raw)).hexdigest() != digest:
                        hash_states.append("FAIL")
                        error("EVIDENCE_HASH_MISMATCH", "Computed SHA-256 does not match the evidence record.")
                    else:
                        hash_states.append("PASS")
            evidence_provenance = "PASS" if provenance_ok else "FAIL"
            hash_verification = "FAIL" if "FAIL" in hash_states else ("PASS" if hash_states and all(x == "PASS" for x in hash_states) else "NOT_EVALUATED")
            if decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS") and hash_verification != "PASS":
                error("REQUIRED_EVIDENCE_HASH_UNVERIFIED", "Required evidence hash must be verified against the exact captured bytes before release.")

    # Calendar validation is explicitly applicability-aware.
    calendar_validation = "NOT_REQUIRED" if not calendar_required else "NOT_EVALUATED"
    if calendar_supplied:
        if not isinstance(market_calendar, dict):
            calendar_validation = "FAIL"
            error("CALENDAR_INPUT_INCOMPLETE", "Supplied calendar must be an object containing boolean market_open.")
        elif "market_open" not in market_calendar:
            calendar_validation = "NOT_EVALUATED" if calendar_required else "FAIL"
            error("CALENDAR_INPUT_INCOMPLETE", "Supplied calendar does not contain explicit market_open evidence.")
        elif not isinstance(market_calendar.get("market_open"), bool):
            calendar_validation = "FAIL"
            error("CALENDAR_INPUT_INCOMPLETE", "Supplied calendar market_open must be Boolean.")
        else:
            calendar_validation = "PASS"
            calendar_market_date = market_calendar.get("market_date")
            if calendar_market_date is None:
                if calendar_required:
                    calendar_validation = "FAIL"
                    error("CALENDAR_MARKET_DATE_MISSING", "Contract requires calendar market_date in canonical YYYY-MM-DD format.")
            else:
                calendar_date_valid = False
                if isinstance(calendar_market_date, str):
                    try:
                        calendar_date_valid = date.fromisoformat(calendar_market_date).isoformat() == calendar_market_date
                    except ValueError:
                        calendar_date_valid = False
                if not calendar_date_valid:
                    calendar_validation = "FAIL"
                    error("CALENDAR_MARKET_DATE_INVALID", "Calendar market_date must be a real date in canonical YYYY-MM-DD format.")
                elif calendar_market_date != run_context.get("market_date"):
                    calendar_validation = "FAIL"
                    error("CALENDAR_DATE_MISMATCH", "Calendar market_date does not match run_context.market_date.")
            if pub == "MARKET_CLOSED" and market_calendar["market_open"]:
                calendar_validation = "FAIL"
                error("CALENDAR_STATUS_CONTRADICTION", "Status says MARKET_CLOSED but calendar says market_open=true.")
            if pub == "PUBLISHED" and not market_calendar["market_open"]:
                warning("PUBLISHED_ON_CLOSED_SESSION", "Source reports publication on a closed session; verify special-session or delayed-publication semantics.")
    if calendar_required and calendar_validation != "PASS":
        error("REQUIRED_CALENDAR_NOT_VERIFIED", "Contract requires explicit calendar validation before release.")
    if pub == "MARKET_CLOSED" and ext == "NOT_ATTEMPTED" and val == "NOT_RUN" and decision not in ("HOLD", "REJECT"):
        error("CLOSED_MARKET_NOT_ATTEMPTED_REQUIRES_HOLD", "MARKET_CLOSED + NOT_ATTEMPTED + NOT_RUN must not be released.")

    # Reconciliation gates.
    if reconciliation_required and recon != "MATCHED" and decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"):
        error("REQUIRED_RECONCILIATION_NOT_MATCHED", "Contract requires reconciliation MATCHED before release.")

    integrity = {
        "source_identity": source_identity,
        "evidence_provenance": evidence_provenance,
        "hash_verification": hash_verification,
        "freshness_verification": freshness_verification,
        "calendar_validation": calendar_validation,
    }
    hard_reject_codes = {"INVALID_MUST_REJECT", "RECONCILIATION_MISMATCH_MUST_REJECT"}
    if decision == "REJECT" or any(x["code"] in hard_reject_codes for x in findings):
        eligibility = "REJECT"
    elif findings:
        eligibility = "HOLD"
    elif decision in ENUMS["release_decision"]:
        eligibility = decision
    else:
        eligibility = "HOLD"
    result = {
        "dataset_key": dataset_key,
        "source_id": source_id,
        "declared_release_decision": decision,
        "policy_result": "FAIL" if findings else ("PASS_WITH_WARNINGS" if warnings else "PASS"),
        "release_eligibility": eligibility,
        "integrity": integrity,
        "findings": findings,
        "warnings": warnings,
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
        if not values:
            return "NOT_EVALUATED"
        if "FAIL" in values:
            return "FAIL"
        if "NOT_EVALUATED" in values:
            return "NOT_EVALUATED"
        if all(v == "NOT_REQUIRED" for v in values):
            return "NOT_REQUIRED"
        if all(v in ("PASS", "NOT_REQUIRED") for v in values):
            return "PASS" if "PASS" in values else "NOT_REQUIRED"
        return "NOT_EVALUATED"

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
        # A declared acceptance is not reportable if any required integrity gate is unresolved.
        if eligibility in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"):
            for key in INTEGRITY_KEYS:
                state = aggregate(key)
                if state in ("FAIL", "NOT_EVALUATED"):
                    eligibility = "HOLD"
                    break
    return {
        "package": PACKAGE_NAME,
        "policy_version": POLICY_VERSION,
        "generated_at_utc": generated,
        "execution": {"result": "PASS" if failed == 0 else "FAIL", "policies_evaluated": len(results),
                       "passed": passed, "failed": failed,
                       "warnings": sum(len(r.get("warnings", [])) for r in results)},
        "integrity": {key: aggregate(key) for key in INTEGRITY_KEYS},
        "release_eligibility": eligibility,
        "findings": findings,
    }


def write_report(report: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
