"""Generate a synthetic, non-production M1.3 policy demonstration report."""
import json
from pathlib import Path
from wsh_m1_3.policy_engine import evaluate_dataset, build_report, write_report

RUN = "123e4567-e89b-12d3-a456-426614174000"
STATUS = {
    "run_id": RUN,
    "source_id": "sensibull",
    "dataset_key": "sensibull.market_participants.fii_dii.core",
    "publication_status": "PUBLISHED",
    "extraction_status": "SUCCESS",
    "validation_status": "VALID",
    "freshness_status": "FRESH",
    "reconciliation_status": "NOT_AVAILABLE",
    "release_decision": "ACCEPT",
    "updated_at_utc": "2026-11-23T03:05:00Z",
}
CONTRACT = {
    "source_id": "sensibull",
    "dataset_key": STATUS["dataset_key"],
    "evidence": {"raw_capture_required": False},
    "validation": {"reconciliation_rule": None, "calendar_required": False},
    "freshness": {"max_age_seconds": 3600},
}
REGISTRY = {"source_id": "sensibull", "dataset_key": STATUS["dataset_key"]}
RUN_CONTEXT = {"run_id": RUN, "run_started_at_utc": "2026-11-23T03:00:00Z", "market_date": "2026-11-23"}


def main():
    result = evaluate_dataset(STATUS, contract=CONTRACT, registry=REGISTRY, run_context=RUN_CONTEXT,
                              now_utc="2026-11-23T03:10:00Z")
    report = build_report([result], "2026-11-23T03:10:00Z")
    out = Path(__file__).resolve().parent / "WSH_M1_3_POLICY_VALIDATION_REPORT.json"
    write_report(report, out)
    print(json.dumps(report, indent=2))
    return 0 if (
        report["execution"]["result"] == "PASS"
        and report["release_eligibility"] == "ACCEPT"
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
