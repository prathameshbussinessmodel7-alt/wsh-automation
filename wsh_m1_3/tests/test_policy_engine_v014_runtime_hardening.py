import hashlib
import unittest
from wsh_m1_3.policy_engine import evaluate_dataset

RUN = "123e4567-e89b-12d3-a456-426614174000"
DATASET = "sensibull.market_participants.fii_dii.core"
BASE = {
    "run_id": RUN, "source_id": "sensibull", "dataset_key": DATASET,
    "publication_status": "PUBLISHED", "extraction_status": "SUCCESS",
    "validation_status": "VALID", "freshness_status": "FRESH",
    "reconciliation_status": "NOT_AVAILABLE", "release_decision": "ACCEPT",
    "updated_at_utc": "2026-11-23T03:05:00Z",
}
CONTRACT = {
    "source_id": "sensibull", "dataset_key": DATASET,
    "evidence": {"raw_capture_required": False},
    "validation": {"reconciliation_rule": None, "calendar_required": False},
    "freshness": {"max_age_seconds": 3600},
}
REGISTRY = {"source_id": "sensibull", "dataset_key": DATASET}
CONTEXT = {"run_id": RUN, "run_started_at_utc": "2026-11-23T03:00:00Z", "market_date": "2026-11-23"}
NOW = "2026-11-23T03:10:00Z"

def evaluate(status=None, contract=None, **kwargs):
    return evaluate_dataset(
        dict(BASE if status is None else status),
        contract=dict(CONTRACT if contract is None else contract),
        registry=dict(REGISTRY), run_context=dict(CONTEXT), now_utc=NOW, **kwargs
    )

def codes(result):
    return [item["code"] for item in result["findings"]]

class RuntimeHardeningTests(unittest.TestCase):
    def test_none_optional_inputs_preserve_valid_control(self):
        result = evaluate(contract=None, evidence_records=None, evidence_bytes=None, market_calendar=None)
        self.assertEqual(result["policy_result"], "PASS")

    def test_invalid_now_utc_is_controlled_finding(self):
        result = evaluate_dataset(dict(BASE), contract=dict(CONTRACT), registry=dict(REGISTRY),
            run_context=dict(CONTEXT), now_utc="not-a-timestamp")
        self.assertIn("NOW_TIMESTAMP_INVALID", codes(result))
        self.assertEqual(result["policy_result"], "FAIL")

    def test_non_dict_evidence_record_is_controlled_finding(self):
        required = dict(CONTRACT)
        required["evidence"] = {"raw_capture_required": True}
        result = evaluate(contract=required, evidence_records=["not-an-object"])
        self.assertIn("EVIDENCE_RECORD_INVALID", codes(result))

    def test_non_bytes_evidence_payload_is_controlled_finding(self):
        evidence = {"evidence_id": "evidence-1", "run_id": RUN, "source_id": "sensibull",
            "dataset_key": DATASET, "captured_at_utc": NOW,
            "sha256": hashlib.sha256(b"payload").hexdigest()}
        required = dict(CONTRACT)
        required["evidence"] = {"raw_capture_required": True}
        result = evaluate(contract=required, evidence_records=[evidence],
            evidence_bytes={"evidence-1": "payload"})
        self.assertIn("EVIDENCE_BYTES_INPUT_INVALID", codes(result))

    def test_string_raw_capture_boolean_is_rejected(self):
        invalid = dict(CONTRACT)
        invalid["evidence"] = {"raw_capture_required": "false"}
        self.assertIn("RAW_CAPTURE_REQUIRED_INVALID", codes(evaluate(contract=invalid)))

    def test_string_calendar_required_is_rejected(self):
        invalid = dict(CONTRACT)
        invalid["validation"] = {"reconciliation_rule": None, "calendar_required": "false"}
        self.assertIn("CALENDAR_REQUIRED_INVALID", codes(evaluate(contract=invalid)))

    def test_invalid_reconciliation_rule_type_is_rejected(self):
        invalid = dict(CONTRACT)
        invalid["validation"] = {"reconciliation_rule": True, "calendar_required": False}
        self.assertIn("RECONCILIATION_RULE_INVALID", codes(evaluate(contract=invalid)))

    def test_named_reconciliation_rule_remains_supported(self):
        valid = dict(CONTRACT)
        valid["validation"] = {"reconciliation_rule": "compare-authoritative", "calendar_required": False}
        result = evaluate(status=dict(BASE, reconciliation_status="MATCHED"), contract=valid)
        self.assertNotIn("RECONCILIATION_RULE_INVALID", codes(result))
        self.assertEqual(result["policy_result"], "PASS")

    def test_explicit_reject_is_not_downgraded_by_other_findings(self):
        invalid_status = dict(BASE, source_id="", release_decision="REJECT")
        result = evaluate(status=invalid_status)
        self.assertIn("SOURCE_ID_MISSING", codes(result))
        self.assertEqual(result["release_eligibility"], "REJECT")
        self.assertEqual(result["policy_result"], "FAIL")

    def test_non_string_evidence_id_is_controlled_finding(self):
        evidence = {"evidence_id": ["not", "hashable"], "run_id": RUN,
            "source_id": "sensibull", "dataset_key": DATASET,
            "captured_at_utc": NOW, "sha256": hashlib.sha256(b"payload").hexdigest()}
        required = dict(CONTRACT)
        required["evidence"] = {"raw_capture_required": True}
        result = evaluate(contract=required, evidence_records=[evidence], evidence_bytes={})
        self.assertIn("EVIDENCE_ID_INVALID", codes(result))
        self.assertEqual(result["policy_result"], "FAIL")

    def test_extremely_large_integer_max_age_is_controlled_finding(self):
        invalid = dict(CONTRACT)
        invalid["freshness"] = {"max_age_seconds": 10 ** 10000}
        result = evaluate(contract=invalid)
        self.assertIn("FRESHNESS_POLICY_INVALID", codes(result))
        self.assertEqual(result["policy_result"], "FAIL")

    def test_invalid_now_utc_does_not_fallback_to_wall_clock(self):
        result = evaluate_dataset(dict(BASE), contract=dict(CONTRACT),
            registry=dict(REGISTRY), run_context=dict(CONTEXT),
            evidence_records=None, evidence_bytes=None, now_utc="not-a-timestamp")
        self.assertIn("NOW_TIMESTAMP_INVALID", codes(result))
        self.assertEqual(result["integrity"]["freshness_verification"], "FAIL")
        self.assertEqual(result["policy_result"], "FAIL")

    def test_invalid_now_utc_with_required_evidence_is_controlled(self):
        evidence = {"evidence_id": "evidence-1", "run_id": RUN,
            "source_id": "sensibull", "dataset_key": DATASET,
            "captured_at_utc": NOW, "sha256": hashlib.sha256(b"payload").hexdigest()}
        required = dict(CONTRACT)
        required["evidence"] = {"raw_capture_required": True}
        result = evaluate_dataset(dict(BASE), contract=required,
            registry=dict(REGISTRY), run_context=dict(CONTEXT),
            evidence_records=[evidence], evidence_bytes={"evidence-1": b"payload"},
            now_utc="not-a-timestamp")
        self.assertIn("NOW_TIMESTAMP_INVALID", codes(result))
        self.assertIn("EVIDENCE_TIME_REFERENCE_UNAVAILABLE", codes(result))
        self.assertEqual(result["policy_result"], "FAIL")

if __name__ == "__main__":
    unittest.main()
