"""WSH R7.5 integrated evidence orchestrator regression tests."""

import unittest

from wsh_r7_integrated_validator import evaluate_dataset, evaluate_batch, VERSION


DATASET = "CCIL_FOREX"
RECORDS = [
    {"date": "2026-11-10", "rate": "83.25"},
    {"date": "2026-11-10", "rate": "83.30"},
]
METADATA = {
    "url": "https://www.ccilindia.com/",
    "dataset": DATASET,
    "captured_at": "2026-11-10T10:30:00+05:30",
    "publication_date": "2026-11-10",
}
FIELD_SPECS = {
    "date": {
        "definition": "Source-reported record date.",
        "unit": "calendar_date",
        "evidence_reference": "CCIL source table date field",
        "value_type": "text",
        "required": True,
    },
    "rate": {
        "definition": "Source-reported rate.",
        "unit": "source_defined_rate_unit",
        "evidence_reference": "CCIL source methodology documentation",
        "value_type": "numeric",
        "required": True,
    },
}


def run(**overrides):
    args = {
        "dataset": DATASET,
        "records": RECORDS,
        "source_metadata": METADATA,
        "field_specs": FIELD_SPECS,
        "raw_payload": "captured raw CCIL payload",
    }
    args.update(overrides)
    return evaluate_dataset(**args)


class R75IntegratedValidatorTests(unittest.TestCase):

    def test_version(self):
        self.assertEqual(VERSION, "WSH_R7.5")

    def test_complete_evidence_can_pass(self):
        result = run()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["acceptance_eligible"])

    def test_missing_raw_payload_is_not_accepted(self):
        result = run(raw_payload=None)
        self.assertNotEqual(result["status"], "PASS")
        self.assertFalse(result["acceptance_eligible"])
        self.assertIn(result["layers"]["acquisition"]["status"],
                      {"UNKNOWN", "PARTIAL", "FAIL"})

    def test_missing_publication_date_is_not_inferred(self):
        metadata = dict(METADATA)
        metadata.pop("publication_date")
        result = run(source_metadata=metadata)
        self.assertNotEqual(result["status"], "PASS")
        self.assertEqual(
            result["evidence"]["provenance_report"]["publication_date"]["status"],
            "UNKNOWN",
        )

    def test_missing_field_contract_is_not_accepted(self):
        result = run(field_specs={})
        self.assertNotEqual(result["status"], "PASS")
        self.assertFalse(result["acceptance_eligible"])

    def test_conflicting_record_dates_fail(self):
        records = [dict(RECORDS[0]), dict(RECORDS[1])]
        records[1]["date"] = "2026-11-11"
        result = run(records=records)
        self.assertEqual(result["layers"]["provenance"]["status"], "FAIL")
        self.assertFalse(result["acceptance_eligible"])

    def test_dataset_identity_mismatch_is_rejected(self):
        result = run(dataset="CCIL_GSEC")
        self.assertNotEqual(result["status"], "PASS")

    def test_unsupported_dataset_is_unknown(self):
        result = run(dataset="SENSIBULL_OPTION_CHAIN")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["acceptance_eligible"])

    def test_batch_preserves_each_dataset_result(self):
        result = evaluate_batch([
            {
                "dataset": DATASET,
                "records": RECORDS,
                "source_metadata": METADATA,
                "field_specs": FIELD_SPECS,
                "raw_payload": "captured raw CCIL payload",
            },
            {
                "dataset": "SENSIBULL_OPTION_CHAIN",
                "records": [],
                "source_metadata": {},
                "field_specs": {},
            },
        ])
        self.assertEqual(result["dataset_count"], 2)
        self.assertEqual(len(result["results"]), 2)
        self.assertFalse(result["results"][1]["acceptance_eligible"])

    def test_capture_time_is_not_used_as_publication_date(self):
        metadata = dict(METADATA)
        metadata.pop("publication_date")
        result = run(source_metadata=metadata)
        policy = result["evidence"]["provenance_report"]["inference_policy"]
        self.assertFalse(policy["capture_time_used_as_publication_date"])
        self.assertFalse(policy["weekday_used_to_infer_publication"])

    def test_result_exposes_all_layer_reasons(self):
        result = run()
        self.assertEqual(
            set(result["layers"]),
            {"acquisition", "provenance", "semantic", "cross_layer"},
        )
        for layer in result["layers"].values():
            self.assertIn("status", layer)
            self.assertIn("reason", layer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
