from __future__ import annotations
import json
from pathlib import Path
from .policy_engine import evaluate_dataset, build_report, write_report

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = {
    "run_id": "123e4567-e89b-12d3-a456-426614174000",
    "source_id": "sensibull",
    "dataset_key": "sensibull.market_participants.fii_dii.core",
    "publication_status": "PUBLISHED",
    "extraction_status": "SUCCESS",
    "validation_status": "VALID",
    "freshness_status": "FRESH",
    "reconciliation_status": "NOT_AVAILABLE",
    "release_decision": "ACCEPT",
    "updated_at_utc": "2026-11-23T03:05:00Z"
}

def main():
    result = evaluate_dataset(FIXTURE)
    report = build_report([result])
    write_report(report, ROOT / "WSH_M1_3_POLICY_VALIDATION_REPORT.json")
    print(json.dumps(report, indent=2))
    return 0 if report["execution"]["result"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
