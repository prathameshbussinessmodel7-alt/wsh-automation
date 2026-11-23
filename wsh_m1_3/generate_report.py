import json
from pathlib import Path
from wsh_m1_3.policy_engine import evaluate_dataset, build_report, write_report

RUN = "123e4567-e89b-12d3-a456-426614174000"
status = {
    "run_id": RUN, "source_id": "sensibull",
    "dataset_key": "sensibull.market_participants.fii_dii.core",
    "publication_status": "PUBLISHED", "extraction_status": "SUCCESS",
    "validation_status": "VALID", "freshness_status": "FRESH",
    "reconciliation_status": "NOT_AVAILABLE", "release_decision": "ACCEPT",
    "updated_at_utc": "2026-11-23T03:05:00Z"
}
result = evaluate_dataset(status)
report = build_report([result])
out = Path(__file__).resolve().parent / "WSH_M1_3_POLICY_VALIDATION_REPORT.json"
write_report(report, out)
print(json.dumps(report, indent=2))
