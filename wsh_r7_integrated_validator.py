"""
WSH R7.5 — Integrated Evidence Validation Orchestrator.

Connects the existing R7.2 provenance validator, R7.3 semantic validator,
and R7.4 acceptance gate. This module does not collect data and does not
modify the institutional collector.

Missing evidence remains UNKNOWN; incomplete evidence remains PARTIAL;
contradictory evidence remains FAIL. Only final PASS is acceptance-eligible.
Sensibull is intentionally outside scope.
"""

from __future__ import annotations

import hashlib
from typing import Any

import wsh_r7_provenance as provenance_engine
import wsh_r7_semantic as semantic_engine
import wsh_r7_evidence_gate as evidence_gate


VERSION = "WSH_R7.5"

SUPPORTED_DATASETS = set(evidence_gate.SUPPORTED_DATASETS)

# Explicit, narrow adapter: R7.3 names the cash dataset NSE_FII_DII.
SEMANTIC_DATASET_MAP = {
    "NSE_FII_DII_CASH": "NSE_FII_DII",
}


def _status(value: Any) -> str:
    if not isinstance(value, str):
        return "UNKNOWN"
    value = value.strip().upper()
    return value if value in {"PASS", "PARTIAL", "UNKNOWN", "FAIL"} else "UNKNOWN"


def _source_for_dataset(dataset: str, supplied_source: str | None) -> str:
    if supplied_source:
        return supplied_source.strip().upper()
    if dataset.startswith("NSE_"):
        return "NSE"
    if dataset.startswith("CCIL_"):
        return "CCIL"
    return "UNKNOWN"


def _payload_hash(raw_payload: Any) -> str | None:
    if raw_payload is None:
        return None
    if isinstance(raw_payload, bytes):
        raw = raw_payload
    elif isinstance(raw_payload, str):
        raw = raw_payload.encode("utf-8")
    else:
        return None
    return hashlib.sha256(raw).hexdigest()


def evaluate_dataset(
    dataset: str,
    records: list[dict] | None,
    source_metadata: dict | None,
    field_specs: dict | None,
    *,
    raw_payload: str | bytes | None = None,
    source_name: str | None = None,
    relationships: list[dict] | None = None,
    acquisition_status: str = "PASS",
) -> dict:
    """
    Run R7.2 -> R7.3 -> R7.4 for one dataset.

    source_metadata must contain an explicit source URL and, where available,
    explicit capture/publication/as-of date fields. `records` must be the
    records actually checked. `raw_payload` is the exact captured payload
    whose SHA-256 is recorded.

    R7.3 field_specs must document each field's definition and unit.
    No units, dates, or field definitions are inferred here.
    """
    dataset_name = (
        dataset.strip().upper()
        if isinstance(dataset, str) and dataset.strip()
        else "UNKNOWN"
    )
    source_records = records if isinstance(records, list) else []
    metadata = source_metadata if isinstance(source_metadata, dict) else {}
    source = _source_for_dataset(dataset_name, source_name)

    if dataset_name not in SUPPORTED_DATASETS:
        unknown = {
            "status": "UNKNOWN",
            "reason": "Dataset is outside the registered R7.5 scope.",
        }
        return {
            "orchestrator_version": VERSION,
            "dataset": dataset_name,
            "status": "UNKNOWN",
            "acceptance_eligible": False,
            "layers": {
                "acquisition": unknown,
                "provenance": unknown,
                "semantic": unknown,
                "cross_layer": unknown,
            },
            "reason": "Unsupported dataset; no acceptance decision made.",
        }

    # R7.2: validate source domain, capture timestamp and explicit dates.
    provenance_report = provenance_engine.inspect_evidence(
        metadata, source, source_records
    )

    # R7.3: validate the actual records against documented contracts.
    semantic_dataset = SEMANTIC_DATASET_MAP.get(dataset_name, dataset_name)
    semantic_report = semantic_engine.validate_dataset(
        semantic_dataset,
        source_records,
        field_specs if isinstance(field_specs, dict) else {},
        relationships=relationships,
    )

    # Adapt R7.2 output to the explicit R7.4 provenance contract.
    source_identity_status = _status(
        provenance_report.get("source_identity", {}).get("status")
    )
    declared_dataset = metadata.get("dataset")
    if declared_dataset is not None:
        if str(declared_dataset).strip().upper() != dataset_name:
            source_identity_status = "FAIL"
    capture = provenance_report.get("capture_timestamp", {})
    record_date_status = _status(
        provenance_report.get("record_date_consistency", {}).get("status")
    )
    publication_date_status = _status(
        provenance_report.get("publication_date", {}).get("status")
    )

    gate_provenance = {
        "dataset": dataset_name,
        "status": "PASS",
        "source_identity_status": source_identity_status,
        "capture_timestamp_utc": capture.get("utc"),
        "record_date_status": record_date_status,
        "publication_date_status": publication_date_status,
    }

    payload_digest = _payload_hash(raw_payload)
    source_url = (
        metadata.get("endpoint")
        or metadata.get("url")
        or metadata.get("page_url")
        or metadata.get("source_url")
    )

    gate_acquisition = {
        "dataset": dataset_name,
        "status": _status(acquisition_status),
        "source_url": source_url,
        "raw_payload_sha256": payload_digest,
        "records_count": len(source_records),
    }

    # Keep the R7.3 engine's own dataset label visible, while giving R7.4
    # the canonical dataset identity for cross-layer consistency.
    gate_semantic = dict(semantic_report)
    gate_semantic["dataset"] = dataset_name

    gate_result = evidence_gate.evaluate_dataset_gate(
        dataset_name,
        gate_acquisition,
        gate_provenance,
        gate_semantic,
    )

    layers = gate_result.get("layers", {})
    final_status = _status(gate_result.get("status"))

    return {
        "orchestrator_version": VERSION,
        "dataset": dataset_name,
        "source": source,
        "status": final_status,
        "acceptance_eligible": final_status == "PASS",
        "reason": (
            "All evidence layers passed."
            if final_status == "PASS"
            else "Dataset is not acceptance-eligible; inspect layer results."
        ),
        "layers": layers,
        "evidence": {
            "provenance_version": provenance_report.get("validator_version"),
            "semantic_version": semantic_report.get("validator_version"),
            "gate_version": gate_result.get("validator_version"),
            "semantic_engine_dataset": semantic_dataset,
            "provenance_report": provenance_report,
            "semantic_report": semantic_report,
            "raw_payload_sha256": payload_digest,
            "records_supplied": len(source_records),
        },
    }


def evaluate_batch(datasets: list[dict]) -> dict:
    """Evaluate multiple dataset input packages without hiding individual results."""
    if not isinstance(datasets, list):
        return {
            "orchestrator_version": VERSION,
            "status": "UNKNOWN",
            "dataset_count": 0,
            "results": [],
            "reason": "Batch input must be a list.",
        }

    results = []
    for item in datasets:
        if not isinstance(item, dict):
            results.append({
                "orchestrator_version": VERSION,
                "dataset": "UNKNOWN",
                "status": "UNKNOWN",
                "acceptance_eligible": False,
                "reason": "Dataset input item is malformed.",
            })
            continue
        args = dict(item)
        dataset = args.pop("dataset", None)
        results.append(evaluate_dataset(dataset, **args))

    statuses = [_status(item.get("status")) for item in results]
    overall = evidence_gate._combine_statuses(*statuses) if statuses else "UNKNOWN"

    return {
        "orchestrator_version": VERSION,
        "status": overall,
        "dataset_count": len(results),
        "accepted_count": sum(
            1 for item in results if item.get("acceptance_eligible") is True
        ),
        "results": results,
        "reason": "Batch status is the most restrictive dataset status.",
    }


if __name__ == "__main__":
    print("WSH R7.5 Integrated Evidence Orchestrator")
    print("Version:", VERSION)
    print("Registered datasets:", len(SUPPORTED_DATASETS))
    print("Sensibull: excluded")
