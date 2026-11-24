import json
import unittest
from pathlib import Path
from wsh_m1_3.policy_engine import build_report, evaluate_dataset, ENUMS

RUN = "123e4567-e89b-12d3-a456-426614174000"
STATUS = {
    "run_id": RUN, "source_id": "sensibull", "dataset_key": "sensibull.market_participants.fii_dii.core",
    "publication_status": "PUBLISHED", "extraction_status": "SUCCESS", "validation_status": "VALID",
    "freshness_status": "FRESH", "reconciliation_status": "NOT_AVAILABLE", "release_decision": "ACCEPT",
    "updated_at_utc": "2026-11-23T03:05:00Z"
}
CONTRACT = {"source_id": "sensibull", "dataset_key": STATUS["dataset_key"], "evidence": {"raw_capture_required": False},
            "validation": {}, "freshness": {"max_age_seconds": 3600}}
REGISTRY = {"source_id": "sensibull", "dataset_key": STATUS["dataset_key"]}
CONTEXT = {"run_id": RUN, "market_date": "2026-11-23"}


class ReportTests(unittest.TestCase):
    def test_empty_report_is_not_evaluated(self):
        report = build_report([], "2026-11-23T00:00:00Z")
        self.assertEqual(report["release_eligibility"], "NOT_EVALUATED")
        self.assertEqual(report["execution"]["policies_evaluated"], 0)

    def test_failed_result_fails_report(self):
        result = {"policy_result": "FAIL", "release_eligibility": "HOLD",
                  "integrity": {k: "FAIL" for k in ("source_identity", "evidence_provenance", "hash_verification", "freshness_verification", "calendar_validation")},
                  "findings": [{"code": "TEST", "severity": "ERROR"}], "warnings": []}
        self.assertEqual(build_report([result], "2026-11-23T00:00:00Z")["execution"]["result"], "FAIL")

    def test_all_accept_aggregates_accept(self):
        result = {"policy_result": "PASS", "release_eligibility": "ACCEPT",
                  "integrity": {"source_identity": "PASS", "evidence_provenance": "NOT_REQUIRED", "hash_verification": "NOT_REQUIRED",
                                "freshness_verification": "PASS", "calendar_validation": "NOT_REQUIRED"},
                  "findings": [], "warnings": []}
        self.assertEqual(build_report([result], "2026-11-23T00:00:00Z")["release_eligibility"], "ACCEPT")

    def test_aggregate_unresolved_integrity_forces_hold(self):
        result = {"policy_result": "PASS", "release_eligibility": "ACCEPT",
                  "integrity": {"source_identity": "PASS", "evidence_provenance": "NOT_REQUIRED", "hash_verification": "NOT_REQUIRED",
                                "freshness_verification": "NOT_EVALUATED", "calendar_validation": "NOT_REQUIRED"},
                  "findings": [], "warnings": []}
        self.assertEqual(build_report([result])["release_eligibility"], "HOLD")

    def test_json_policy_rules_match_engine_enumerations_and_release_gates(self):
        rules_path = Path(__file__).resolve().parents[1] / "policy_rules.json"
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
        self.assertEqual({k: sorted(v) for k, v in rules["status_dimensions"].items()}, {k: sorted(v) for k, v in ENUMS.items()})
        for decision in ("ACCEPT", "ACCEPT_WITH_LIMITATIONS"):
            self.assertTrue(rules["release_policy"][decision]["requires_verified_identity"])
            self.assertTrue(rules["release_policy"][decision]["requires_verified_freshness"])
            self.assertEqual(rules["release_policy"][decision]["reconciliation_status"], ["MATCHED", "NOT_AVAILABLE"])
        self.assertTrue(rules["calendar_policy"]["required_when_contract_declares_calendar_rule"])
        self.assertTrue(rules["calendar_policy"]["requires_explicit_boolean_market_open"])
        self.assertTrue(rules["calendar_policy"]["requires_canonical_market_date"])
        self.assertTrue(rules["calendar_policy"]["do_not_infer_market_open_or_closed_from_weekday_alone"])

    def test_generated_report_matches_schema(self):
        from jsonschema import Draft202012Validator, FormatChecker
        result = evaluate_dataset(STATUS, contract=CONTRACT, registry=REGISTRY, run_context=CONTEXT,
                                  now_utc="2026-11-23T03:10:00Z")
        report = build_report([result], "2026-11-23T03:10:00Z")
        schema = json.loads((Path(__file__).resolve().parents[1] / "policy_report.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(report))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
