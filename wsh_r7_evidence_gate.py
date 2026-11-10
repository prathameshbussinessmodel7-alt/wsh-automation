"""
WSH R7.4 â€” Integrated Evidence Acceptance Gate.

Purpose:
Evaluate acquisition, provenance, semantic and cross-layer
evidence before a dataset can be marked acceptance-eligible.

Status vocabulary:
PASS
PARTIAL
UNKNOWN
FAIL

Important:
- Missing evidence is UNKNOWN.
- Incomplete evidence is PARTIAL.
- Demonstrably invalid or contradictory evidence is FAIL.
- Only a complete PASS across all layers is acceptance-eligible.
- Capture time is not publication date.
- No publication date is inferred from weekdays.
- No units or field definitions are inferred from magnitudes or headers.
- Sensibull is outside this validator's scope.
"""

from datetime import datetime
from urllib.parse import urlparse
import re


VALID_STATUSES = {"PASS", "PARTIAL", "UNKNOWN", "FAIL"}

STATUS_PRIORITY = {
    "PASS": 0,
    "UNKNOWN": 1,
    "PARTIAL": 2,
    "FAIL": 3,
}

SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")

SUPPORTED_DATASETS = {
    "NSE_FII_DII_CASH",
    "NSE_PARTICIPANT_DERIVATIVES",
    "NSE_FII_DII_HISTORY",
    "CCIL_MONEY_MARKET",
    "CCIL_GSEC",
    "CCIL_FOREX",
    "CCIL_DERIVATIVES",
}


def _normalise_status(value):
    """Return a normalized evidence status."""
    if not isinstance(value, str):
        return "UNKNOWN"

    status = value.strip().upper()

    if status not in VALID_STATUSES:
        return "UNKNOWN"

    return status


def _result(status, reason):
    """Standard layer result."""
    return {
        "status": _normalise_status(status),
        "reason": str(reason),
    }


def _combine_statuses(*statuses):
    """Return the most restrictive status."""
    normalized = [
        _normalise_status(status)
        for status in statuses
    ]

    if not normalized:
        return "UNKNOWN"

    return max(
        normalized,
        key=lambda item: STATUS_PRIORITY[item],
    )


def _is_valid_https_url(value):
    """Validate that a source URL is a usable HTTPS URL."""
    if not isinstance(value, str) or not value.strip():
        return False

    try:
        parsed = urlparse(value.strip())
        return (
            parsed.scheme.lower() == "https"
            and bool(parsed.netloc)
            and "." in parsed.netloc
        )
    except Exception:
        return False


def _parse_aware_timestamp(value):
    """
    Parse an ISO timestamp requiring timezone information.

    A naive timestamp is not accepted as UTC.
    """
    if not isinstance(value, str) or not value.strip():
        return None

    candidate = value.strip()

    try:
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"

        parsed = datetime.fromisoformat(candidate)

        if parsed.tzinfo is None:
            return None

        if parsed.utcoffset() is None:
            return None

        return parsed

    except (ValueError, TypeError, OverflowError):
        return None


def _validate_acquisition(dataset, acquisition):
    """Validate acquisition identity and raw-payload integrity."""

    if not isinstance(acquisition, dict) or not acquisition:
        return _result(
            "UNKNOWN",
            "Acquisition evidence is missing.",
        )

    supplied_dataset = acquisition.get("dataset")

    if not supplied_dataset:
        return _result(
            "UNKNOWN",
            "Acquisition dataset identity is missing.",
        )

    if str(supplied_dataset).strip().upper() != dataset:
        return _result(
            "FAIL",
            "Acquisition dataset identity conflicts with requested dataset.",
        )

    status = _normalise_status(
        acquisition.get("status")
    )

    if status != "PASS":
        return _result(
            status,
            "Acquisition status is not PASS.",
        )

    source_url = acquisition.get("source_url")

    if not _is_valid_https_url(source_url):
        return _result(
            "UNKNOWN",
            "A valid HTTPS source URL is not established.",
        )

    payload_hash = acquisition.get("raw_payload_sha256")

    if not isinstance(payload_hash, str) or not payload_hash.strip():
        return _result(
            "UNKNOWN",
            "Raw payload SHA-256 evidence is missing.",
        )

    if not SHA256_PATTERN.fullmatch(payload_hash.strip()):
        return _result(
            "FAIL",
            "Raw payload SHA-256 evidence is malformed.",
        )

    records_count = acquisition.get("records_count")

    if (
        isinstance(records_count, bool)
        or not isinstance(records_count, int)
    ):
        return _result(
            "UNKNOWN",
            "Acquisition record count is missing or invalid.",
        )

    if records_count < 0:
        return _result(
            "FAIL",
            "Acquisition record count cannot be negative.",
        )

    if records_count == 0:
        return _result(
            "UNKNOWN",
            "No acquired records are available for validation.",
        )

    return _result(
        "PASS",
        "Source identity, payload hash and acquisition count are present.",
    )


def _validate_provenance(dataset, provenance):
    """Validate source identity and date provenance."""

    if not isinstance(provenance, dict) or not provenance:
        return _result(
            "UNKNOWN",
            "Provenance evidence is missing.",
        )

    supplied_dataset = provenance.get("dataset")

    if not supplied_dataset:
        return _result(
            "UNKNOWN",
            "Provenance dataset identity is missing.",
        )

    if str(supplied_dataset).strip().upper() != dataset:
        return _result(
            "FAIL",
            "Provenance dataset identity conflicts with requested dataset.",
        )

    base_status = _normalise_status(
        provenance.get("status")
    )

    if base_status != "PASS":
        return _result(
            base_status,
            "Provenance evidence is not fully validated.",
        )

    source_status = _normalise_status(
        provenance.get("source_identity_status")
    )

    if source_status != "PASS":
        return _result(
            source_status,
            "Source identity is not independently established.",
        )

    capture_timestamp = provenance.get(
        "capture_timestamp_utc"
    )

    parsed_timestamp = _parse_aware_timestamp(
        capture_timestamp
    )

    if parsed_timestamp is None:
        return _result(
            "UNKNOWN",
            "Capture timestamp is missing, invalid or timezone-naive.",
        )

    record_date_status = _normalise_status(
        provenance.get("record_date_status")
    )

    if record_date_status != "PASS":
        return _result(
            record_date_status,
            "Record-date evidence is not fully established.",
        )

    publication_date_status = _normalise_status(
        provenance.get("publication_date_status")
    )

    if publication_date_status != "PASS":
        return _result(
            publication_date_status,
            "Publication-date evidence is not fully established.",
        )

    return _result(
        "PASS",
        "Source identity, capture timestamp, record date and publication date are validated.",
    )


def _validate_semantic(dataset, semantic):
    """Validate documented field semantics and numeric coverage."""

    if not isinstance(semantic, dict) or not semantic:
        return _result(
            "UNKNOWN",
            "Semantic evidence is missing.",
        )

    supplied_dataset = semantic.get("dataset")

    if not supplied_dataset:
        return _result(
            "UNKNOWN",
            "Semantic dataset identity is missing.",
        )

    if str(supplied_dataset).strip().upper() != dataset:
        return _result(
            "FAIL",
            "Semantic dataset identity conflicts with requested dataset.",
        )

    status = _normalise_status(
        semantic.get("status")
    )

    if status != "PASS":
        return _result(
            status,
            "Semantic validation is not fully passed.",
        )

    records_checked = semantic.get("records_checked")

    if (
        isinstance(records_checked, bool)
        or not isinstance(records_checked, int)
    ):
        return _result(
            "UNKNOWN",
            "Semantic record coverage is missing or invalid.",
        )

    if records_checked <= 0:
        return _result(
            "UNKNOWN",
            "No records have been semantically checked.",
        )

    policy = semantic.get("policy")

    if not isinstance(policy, dict):
        return _result(
            "UNKNOWN",
            "Documented semantic policy is missing.",
        )

    required_policy = (
        "units_inferred_from_magnitude",
        "definitions_inferred_from_header_names",
        "source_evidence_required_for_semantic_pass",
    )

    for key in required_policy:
        if key not in policy:
            return _result(
                "UNKNOWN",
                "Required semantic policy field is missing: " + key,
            )

        if not isinstance(policy[key], bool):
            return _result(
                "UNKNOWN",
                "Semantic policy field is not boolean: " + key,
            )

    if policy["units_inferred_from_magnitude"]:
        return _result(
            "FAIL",
            "Unit inference from numerical magnitude is prohibited.",
        )

    if policy["definitions_inferred_from_header_names"]:
        return _result(
            "FAIL",
            "Field-definition inference from header names is prohibited.",
        )

    if not policy["source_evidence_required_for_semantic_pass"]:
        return _result(
            "FAIL",
            "Semantic PASS must require source evidence.",
        )

    return _result(
        "PASS",
        "Documented semantic policy and record coverage are validated.",
    )


def _validate_cross_layer(
    acquisition,
    provenance,
    semantic,
):
    """Check consistency between the independently validated layers."""

    if not all(
        isinstance(item, dict) and item
        for item in (acquisition, provenance, semantic)
    ):
        return _result(
            "UNKNOWN",
            "One or more layers are unavailable for cross-layer validation.",
        )

    # Identity contradictions remain FAIL, even when no records were acquired.
    identities = [
        acquisition.get("dataset"),
        provenance.get("dataset"),
        semantic.get("dataset"),
    ]

    if any(not item for item in identities):
        return _result(
            "UNKNOWN",
            "A layer dataset identity is missing.",
        )

    normalized = {
        str(item).strip().upper()
        for item in identities
    }

    if len(normalized) != 1:
        return _result(
            "FAIL",
            "Dataset identities conflict across evidence layers.",
        )

    acquisition_count = acquisition.get("records_count")
    semantic_count = semantic.get("records_checked")

    if (
        isinstance(acquisition_count, bool)
        or not isinstance(acquisition_count, int)
        or isinstance(semantic_count, bool)
        or not isinstance(semantic_count, int)
    ):
        return _result(
            "UNKNOWN",
            "Record counts cannot be compared.",
        )

    if acquisition_count < 0 or semantic_count < 0:
        return _result(
            "FAIL",
            "Record counts cannot be negative.",
        )

    # Zero acquired records provide no basis for count reconciliation.
    # This is UNKNOWN, not FAIL; absence alone is not a contradiction.
    if acquisition_count == 0:
        return _result(
            "UNKNOWN",
            "No acquired records are available; cross-layer consistency cannot be established.",
        )

    if acquisition_count != semantic_count:
        return _result(
            "FAIL",
            "Acquired record count and semantically checked record count differ.",
        )

    return _result(
        "PASS",
        "Dataset identity and record counts reconcile across layers.",
    )


def evaluate_dataset_gate(
    dataset,
    acquisition,
    provenance,
    semantic,
):
    """
    Evaluate one dataset through the R7.4 evidence gate.

    Returns:
        validator_version
        dataset
        status
        acceptance_eligible
        layers
    """

    if not isinstance(dataset, str) or not dataset.strip():
        dataset_name = "UNKNOWN"
    else:
        dataset_name = dataset.strip().upper()

    if dataset_name not in SUPPORTED_DATASETS:
        unknown = _result(
            "UNKNOWN",
            "Dataset is not registered in the R7.4 evidence-gate scope.",
        )

        layers = {
            "acquisition": unknown,
            "provenance": unknown,
            "semantic": unknown,
            "cross_layer": unknown,
        }

        return {
            "validator_version": "R7.4",
            "dataset": dataset_name,
            "status": "UNKNOWN",
            "acceptance_eligible": False,
            "layers": layers,
        }

    acquisition_result = _validate_acquisition(
        dataset_name,
        acquisition,
    )

    provenance_result = _validate_provenance(
        dataset_name,
        provenance,
    )

    semantic_result = _validate_semantic(
        dataset_name,
        semantic,
    )

    cross_layer_result = _validate_cross_layer(
        acquisition,
        provenance,
        semantic,
    )

    layers = {
        "acquisition": acquisition_result,
        "provenance": provenance_result,
        "semantic": semantic_result,
        "cross_layer": cross_layer_result,
    }

    final_status = _combine_statuses(
        acquisition_result["status"],
        provenance_result["status"],
        semantic_result["status"],
        cross_layer_result["status"],
    )

    acceptance_eligible = final_status == "PASS"

    return {
        "validator_version": "R7.4",
        "dataset": dataset_name,
        "status": final_status,
        "acceptance_eligible": acceptance_eligible,
        "layers": layers,
    }


if __name__ == "__main__":
    print("WSH R7.4 Evidence Gate")
    print("Validator module loaded successfully.")
    print("Run regression tests with:")
    print("python -m unittest -v test_wsh_r7_evidence_gate.py")
